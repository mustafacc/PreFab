"""Provides the Device class for representing photonic devices."""

import base64
import os
import struct
import warnings
from typing import Optional

import cv2
import gdstk
import matplotlib.cm as cm
import matplotlib.pyplot as plt
import numpy as np
import requests
import toml
from matplotlib.axes import Axes
from matplotlib.lines import Line2D
from matplotlib.patches import Rectangle
from pydantic import BaseModel, Field, conint, root_validator, validator

from . import geometry


class BufferSpec(BaseModel):
    """
    Defines the specifications for a buffer zone around a device.

    This class is used to specify the mode and thickness of a buffer zone that is added
    around the device geometry. The buffer zone can be used for various purposes such as
    providing extra space for device fabrication processes or for ensuring that the
    device is isolated from surrounding structures.

    Parameters
    ----------
    mode : dict[str, str]
        A dictionary that defines the buffer mode for each side of the device
        ('top', 'bottom', 'left', 'right'), where 'constant' is used for isolated
        structures and 'edge' is utilized for preserving the edge, such as for waveguide
        connections.
    thickness : conint(gt=0)
        The thickness of the buffer zone around the device. Must be greater than 0.

    Raises
    ------
    ValueError
        If any of the modes specified in the 'mode' dictionary are not one of the
        allowed values ('constant', 'edge'). Or if the thickness is not greater than 0.

    Example
    -------
    >>> import prefab as pf
    >>> buffer_spec = pf.BufferSpec(
    ...     mode={
    ...         "top": "constant",
    ...         "bottom": "edge",
    ...         "left": "constant",
    ...         "right": "edge"
    ...     },
    ...     thickness=150
    ... )
    """

    mode: dict[str, str] = Field(
        default_factory=lambda: {
            "top": "constant",
            "bottom": "constant",
            "left": "constant",
            "right": "constant",
        }
    )
    thickness: conint(gt=0) = 128

    @validator("mode", pre=True)
    def check_mode(cls, v):
        allowed_modes = ["constant", "edge"]
        if not all(mode in allowed_modes for mode in v.values()):
            raise ValueError(f"Buffer mode must be one of {allowed_modes}, got '{v}'")
        return v


class Device(BaseModel):
    device_array: np.ndarray = Field(...)
    buffer_spec: BufferSpec = Field(default_factory=BufferSpec)

    class Config:
        arbitrary_types_allowed = True

    @property
    def shape(self) -> tuple[int, int]:
        return self.device_array.shape

    def __init__(
        self, device_array: np.ndarray, buffer_spec: Optional[BufferSpec] = None
    ):
        """
        Represents the planar geometry of a photonic device design that will have its
        nanofabrication outcome predicted and/or corrected.

        This class is designed to encapsulate the geometric representation of a photonic
        device, facilitating operations such as padding, normalization, binarization,
        ternarization, trimming, and blurring. These operations are useful for preparing
        the device design for prediction or correction. Additionally, the class provides
        methods for exporting the device representation to various formats, including
        ndarray, image files, and GDSII files, supporting a range of analysis and
        fabrication workflows.

        Parameters
        ----------
        device_array : np.ndarray
            A 2D array representing the planar geometry of the device. This array
            undergoes various transformations to predict or correct the nanofabrication
            process.
        buffer_spec : BufferSpec, optional
            Defines the parameters for adding a buffer zone around the device geometry.
            This buffer zone is needed for providing surrounding context for prediction
            or correction and for ensuring seamless integration with the surrounding
            circuitry. By default, a generous padding is applied to accommodate isolated
            structures.

        Attributes
        ----------
        shape : tuple[int, int]
            The shape of the device array.

        Raises
        ------
        ValueError
            If the provided `device_array` is not a numpy ndarray or is not a 2D array,
            indicating an invalid device geometry.
        """
        super().__init__(
            device_array=device_array, buffer_spec=buffer_spec or BufferSpec()
        )
        self._initial_processing()

    def _initial_processing(self):
        buffer_thickness = self.buffer_spec.thickness
        buffer_mode = self.buffer_spec.mode

        self.device_array = np.pad(
            self.device_array,
            pad_width=((buffer_thickness, 0), (0, 0)),
            mode=buffer_mode["top"],
        )
        self.device_array = np.pad(
            self.device_array,
            pad_width=((0, buffer_thickness), (0, 0)),
            mode=buffer_mode["bottom"],
        )
        self.device_array = np.pad(
            self.device_array,
            pad_width=((0, 0), (buffer_thickness, 0)),
            mode=buffer_mode["left"],
        )
        self.device_array = np.pad(
            self.device_array,
            pad_width=((0, 0), (0, buffer_thickness)),
            mode=buffer_mode["right"],
        )

        self.device_array = self.device_array.astype(np.float32)

    @root_validator(pre=True)
    def check_device_array(cls, values):
        device_array = values.get("device_array")
        if not isinstance(device_array, np.ndarray):
            raise ValueError("device_array must be a numpy ndarray.")
        if device_array.ndim != 2:
            raise ValueError("device_array must be a 2D array.")
        return values

    def is_binary(self) -> bool:
        """
        Check if the device geometry is binary.

        Returns
        -------
        bool
            True if the device geometry is binary, False otherwise.
        """
        unique_values = np.unique(self.device_array)
        return (
            np.array_equal(unique_values, [0, 1])
            or np.array_equal(unique_values, [1, 0])
            or np.array_equal(unique_values, [0])
            or np.array_equal(unique_values, [1])
        )

    def _encode_array(self, array):
        array_shape = struct.pack(">II", len(array), len(array[0]))
        serialized_array = array_shape + array.tobytes()
        encoded_array = base64.b64encode(serialized_array).decode("utf-8")
        return encoded_array

    def _decode_array(self, encoded_array):
        serialized_array = base64.b64decode(encoded_array)
        array = np.frombuffer(serialized_array, dtype=np.float32, offset=8)
        array.shape = struct.unpack(">II", serialized_array[:8])
        return array

    def _predict(
        self,
        model_name: str,
        model_tags: list[str],
        model_type: str,
        binarize: bool = False,
    ) -> "Device":
        if not self.is_binary():
            warnings.warn(
                "The device is not binary. Prediction accuracy will be affected.",
                UserWarning,
                stacklevel=2,
            )

        function_url = "https://prefab-photonics--predict-v1.modal.run"

        predict_data = {
            "device_array": self._encode_array(self.device_array),
            "model_name": model_name,
            "model_tags": model_tags,
            "model_type": model_type,
            "binary": binarize,
        }

        with open(os.path.expanduser("~/.prefab.toml")) as file:
            content = file.readlines()
            for line in content:
                if "access_token" in line:
                    access_token = line.split("=")[1].strip().strip('"')
                if "refresh_token" in line:
                    refresh_token = line.split("=")[1].strip().strip('"')
                    break
        headers = {
            "Authorization": f"Bearer {access_token}",
            "X-Refresh-Token": refresh_token,
        }

        response = requests.post(url=function_url, json=predict_data, headers=headers)

        if response.status_code != 200:
            raise ValueError(response.text)
        else:
            response_data = response.json()
            if "error" in response_data:
                raise ValueError(response_data["error"])
            if "prediction_array" in response_data:
                prediction_array = self._decode_array(response_data["prediction_array"])
                if binarize:
                    prediction_array = geometry.binarize_hard(prediction_array)
            if "new_refresh_token" in response_data:
                prefab_file_path = os.path.expanduser("~/.prefab.toml")
                with open(prefab_file_path, "w", encoding="utf-8") as toml_file:
                    toml.dump(
                        {
                            "access_token": response_data["new_access_token"],
                            "refresh_token": response_data["new_refresh_token"],
                        },
                        toml_file,
                    )
        return self.model_copy(update={"device_array": prediction_array})

    def predict(
        self,
        model_name: str,
        model_tags: list[str],
        binarize: bool = False,
    ) -> "Device":
        """
        Predict the nanofabrication outcome of the device using a specified model.

        This method sends the device geometry to a serverless prediction service, which
        uses a specified machine learning model to predict the outcome of the
        nanofabrication process.

        Parameters
        ----------
        model_name : str
            The name of the model to use for prediction.
        model_tags : list[str]
            A list of tags associated with the model. These tags can be used to specify
            model versions or configurations.
        binarize : bool, optional
            If True, the predicted device geometry will be binarized using a threshold
            method. This is useful for converting probabilistic predictions into binary
            geometries. Defaults to False.

        Returns
        -------
        Device
            A new instance of the Device class with the predicted geometry.

        Raises
        ------
        ValueError
            If the prediction service returns an error or if the response from the
            service cannot be processed correctly.
        """
        return self._predict(
            model_name=model_name,
            model_tags=model_tags,
            model_type="p",
            binarize=binarize,
        )

    def correct(
        self,
        model_name: str,
        model_tags: list[str],
        binarize: bool = True,
    ) -> "Device":
        """
        Correct the nanofabrication outcome of the device using a specified model.

        This method sends the device geometry to a serverless correction service, which
        uses a specified machine learning model to correct the outcome of the
        nanofabrication process. The correction aims to adjust the device geometry to
        compensate for known fabrication errors and improve the accuracy of the final
        device structure.

        Parameters
        ----------
        model_name : str
            The name of the model to use for correction.
        model_tags : list[str]
            A list of tags associated with the model. These tags can be used to specify
            model versions or configurations.
        binarize : bool, optional
            If True, the corrected device geometry will be binarized using a threshold
            method. This is useful for converting probabilistic corrections into binary
            geometries. Defaults to True.

        Returns
        -------
        Device
            A new instance of the Device class with the corrected geometry.

        Raises
        ------
        ValueError
            If the correction service returns an error or if the response from the
            service cannot be processed correctly.
        """
        return self._predict(
            model_name=model_name,
            model_tags=model_tags,
            model_type="c",
            binarize=binarize,
        )

    def semulate(
        self,
        model_name: str,
        model_tags: list[str],
    ) -> "Device":
        """
        Simulate the appearance of the device as if viewed under a Scanning Electron
        Microscope (SEM).

        This method applies a specified machine learning model to transform the device
        geometry into a style that resembles an SEM image. This can be useful for
        visualizing how the device might appear under an SEM, which is often used for
        inspecting the surface and composition of materials at high magnification.

        Parameters
        ----------
        model_name : str
            The name of the model to use for correction.
        model_tags : list[str]
            A list of tags associated with the model. These tags can be used to specify
            model versions or configurations.

        Returns
        -------
        Device
            A new instance of the Device class with its geometry transformed to simulate
            an SEM image style.
        """
        return self._predict(
            model_name=model_name,
            model_tags=model_tags,
            model_type="s",
        )

    def to_ndarray(self) -> np.ndarray:
        """
        Converts the device geometry to a numpy ndarray.

        This method applies the buffer specifications to crop the device array if
        necessary, based on the buffer mode ('edge' or 'constant'). It then returns the
        resulting numpy ndarray representing the device geometry.

        Returns
        -------
        np.ndarray
            The numpy ndarray representation of the device geometry, with any applied
            buffer cropping.
        """
        device_array = np.copy(self.device_array)
        buffer_thickness = self.buffer_spec.thickness
        buffer_mode = self.buffer_spec.mode

        crop_top = buffer_thickness if buffer_mode["top"] == "edge" else 0
        crop_bottom = buffer_thickness if buffer_mode["bottom"] == "edge" else 0
        crop_left = buffer_thickness if buffer_mode["left"] == "edge" else 0
        crop_right = buffer_thickness if buffer_mode["right"] == "edge" else 0

        ndarray = device_array[
            crop_top : device_array.shape[0] - crop_bottom,
            crop_left : device_array.shape[1] - crop_right,
        ]
        return ndarray

    def to_img(self, img_path: str = "prefab_device.png"):
        """
        Exports the device geometry as an image file.

        This method converts the device geometry to a numpy ndarray using `to_ndarray`,
        scales the values to the range [0, 255] for image representation, and saves the
        result as an image file.

        Parameters
        ----------
        img_path : str, optional
            The path where the image file will be saved. If not specified, the image is
            saved as "prefab_device.png" in the current directory.
        """
        cv2.imwrite(img_path, 255 * self.to_ndarray())
        print(f"Saved Device to '{img_path}'")

    def to_gds(
        self,
        gds_path: str = "prefab_device.gds",
        cell_name: str = "prefab_device",
        gds_layer: tuple[int, int] = (1, 0),
        contour_approx_mode: int = 2,
    ):
        """
        Exports the device geometry as a GDSII file.

        This method converts the device geometry into a format suitable for GDSII files.
        The conversion involves contour approximation to simplify the geometry while
        preserving essential features.

        Parameters
        ----------
        gds_path : str, optional
            The path where the GDSII file will be saved. If not specified, the file is
            saved as "prefab_device.gds" in the current directory.
        cell_name : str, optional
            The name of the cell within the GDSII file. If not specified, defaults to
            "prefab_device".
        gds_layer : tuple[int, int], optional
            The layer and datatype to use within the GDSII file. Defaults to (1, 0).
        contour_approx_mode : int, optional
            The mode of contour approximation used during the conversion. Defaults to 2,
            which corresponds to `cv2.CHAIN_APPROX_SIMPLE`, a method that compresses
            horizontal, vertical, and diagonal segments and leaves only their endpoints.
        """
        gdstk_cell = self._device_to_gdstk(
            cell_name=cell_name,
            gds_layer=gds_layer,
            contour_approx_mode=contour_approx_mode,
        )
        gdstk_library = gdstk.Library()
        gdstk_library.add(gdstk_cell)
        gdstk_library.write_gds(outfile=gds_path, max_points=8190)
        print(f"Saved GDS to '{gds_path}'")

    def to_gdstk(
        self,
        cell_name: str = "prefab_device",
        gds_layer: tuple[int, int] = (1, 0),
        contour_approx_mode: int = 2,
    ):
        """
        Converts the device geometry to a GDSTK cell object.

        This method prepares the device geometry for GDSII file export by converting it
        into a GDSTK cell object. GDSTK is a Python module for creating and manipulating
        GDSII layout files. The conversion involves contour approximation to simplify
        the geometry while preserving essential features.

        Parameters
        ----------
        cell_name : str, optional
            The name of the cell to be created. Defaults to "prefab_device".
        gds_layer : tuple[int, int], optional
            The layer and datatype to use within the GDSTK cell. Defaults to (1, 0).
        contour_approx_mode : int, optional
            The mode of contour approximation used during the conversion. Defaults to 2,
            which corresponds to `cv2.CHAIN_APPROX_SIMPLE`, a method that compresses
            horizontal, vertical, and diagonal segments and leaves only their endpoints.

        Returns
        -------
        gdstk.Cell
            The GDSTK cell object representing the device geometry.
        """
        gdstk_cell = self._device_to_gdstk(
            cell_name=cell_name,
            gds_layer=gds_layer,
            contour_approx_mode=contour_approx_mode,
        )
        return gdstk_cell

    def _device_to_gdstk(
        self,
        cell_name: str,
        gds_layer: tuple[int, int],
        contour_approx_mode: int,
    ) -> gdstk.Cell:
        approx_mode_mapping = {
            1: cv2.CHAIN_APPROX_NONE,
            2: cv2.CHAIN_APPROX_SIMPLE,
            3: cv2.CHAIN_APPROX_TC89_L1,
            4: cv2.CHAIN_APPROX_TC89_KCOS,
        }

        contours, hierarchy = cv2.findContours(
            np.flipud(self.to_ndarray()).astype(np.uint8),
            cv2.RETR_TREE,
            approx_mode_mapping[contour_approx_mode],
        )

        hierarchy_polygons = {}
        for idx, contour in enumerate(contours):
            level = 0
            current_idx = idx
            while hierarchy[0][current_idx][3] != -1:
                level += 1
                current_idx = hierarchy[0][current_idx][3]

            if len(contour) > 2:
                contour = contour / 1000
                points = [tuple(point) for point in contour.squeeze().tolist()]
                if level not in hierarchy_polygons:
                    hierarchy_polygons[level] = []
                hierarchy_polygons[level].append(points)

        cell = gdstk.Cell(cell_name)
        processed_polygons = []
        for level in sorted(hierarchy_polygons.keys()):
            operation = "or" if level % 2 == 0 else "xor"
            polygons_to_process = hierarchy_polygons[level]

            if polygons_to_process:
                processed_polygons = gdstk.boolean(
                    polygons_to_process,
                    processed_polygons,
                    operation,
                    layer=gds_layer[0],
                    datatype=gds_layer[1],
                )
        for polygon in processed_polygons:
            cell.add(polygon)

        return cell

    def _plot_base(
        self, show_buffer: bool = True, ax: Optional[Axes] = None, **kwargs
    ) -> Axes:
        if ax is None:
            _, ax = plt.subplots()
        ax.set_ylabel("y (nm)")
        ax.set_xlabel("x (nm)")

        if show_buffer:
            self._add_buffer_visualization(ax)
        return ax

    def plot(
        self, show_buffer: bool = True, ax: Optional[Axes] = None, **kwargs
    ) -> Axes:
        """
        Visualizes the device along with its buffer zones.

        This method plots the device geometry, allowing for the visualization of the
        device along with its buffer zones if specified. The plot can be customized with
        various matplotlib parameters and can be drawn on an existing matplotlib Axes
        object or create a new one if none is provided.

        Parameters
        ----------
        show_buffer : bool, optional
            If True, the buffer zones around the device will be visualized. This can
            help in understanding the spatial context of the device within its buffer.
            By default, it is set to True.
        ax : Optional[Axes], optional
            An existing matplotlib Axes object to draw the device geometry on. If None,
            a new figure and axes will be created. Defaults to None.

        Returns
        -------
        Axes
            The matplotlib Axes object containing the plot. This can be used for further
            customization or saving the plot after the method returns.
        """
        ax = self._plot_base(show_buffer=show_buffer, ax=ax, **kwargs)
        _ = ax.imshow(
            self.device_array,
            extent=[0, self.device_array.shape[1], 0, self.device_array.shape[0]],
            **kwargs,
        )
        return ax

    def plot_contour(
        self,
        linewidth: Optional[int] = None,
        label: Optional[str] = "Device contour",
        show_buffer: bool = True,
        ax: Optional[Axes] = None,
        **kwargs,
    ):
        """
        Visualizes the contour of the device along with optional buffer zones.

        This method plots the contour of the device geometry, emphasizing the edges and
        boundaries of the device. The contour plot can be customized with various
        matplotlib parameters, including line width and color. The plot can be drawn on
        an existing matplotlib Axes object or create a new one if none is provided.

        Parameters
        ----------
        linewidth : Optional[int], optional
            The width of the contour lines. If None, the linewidth is automatically
            determined based on the size of the device array. Defaults to None.
        label : Optional[str], optional
            The label for the contour in the plot legend. Defaults to "Device contour".
        show_buffer : bool, optional
            If True, the buffer zones around the device will be visualized. By default,
            it is set to True.
        ax : Optional[Axes], optional
            An existing matplotlib Axes object to draw the device contour on. If None, a
            new figure and axes will be created. Defaults to None.

        Returns
        -------
        Axes
            The matplotlib Axes object containing the contour plot. This can be used for
            further customization or saving the plot after the method returns.
        """
        ax = self._plot_base(show_buffer=show_buffer, ax=ax, **kwargs)
        kwargs.setdefault("cmap", "spring")
        if linewidth is None:
            linewidth = self.device_array.shape[0] // 100

        contours, _ = cv2.findContours(
            geometry.binarize_hard(self.device_array).astype(np.uint8),
            cv2.RETR_CCOMP,
            cv2.CHAIN_APPROX_SIMPLE,
        )
        contour_array = np.zeros_like(self.device_array, dtype=np.uint8)
        cv2.drawContours(contour_array, contours, -1, (255,), linewidth)
        contour_array = np.ma.masked_equal(contour_array, 0)
        _ = ax.imshow(
            contour_array,
            extent=[0, self.device_array.shape[1], 0, self.device_array.shape[0]],
            **kwargs,
        )

        cmap = cm.get_cmap(kwargs.get("cmap", "spring"))
        legend_proxy = Line2D([0], [0], linestyle="-", color=cmap(1))
        ax.legend([legend_proxy], [label], loc="upper right")
        return ax

    def plot_uncertainty(
        self, show_buffer: bool = True, ax: Optional[Axes] = None, **kwargs
    ):
        """
        Visualizes the uncertainty in the edge positions of the predicted device.

        This method plots the predicted device geometry with an overlay indicating the
        uncertainty associated with the positions of the edges of the device. The
        uncertainty is represented as a gradient, with areas of higher uncertainty
        indicating a greater likelihood of variation in edge position from run to run
        due to inconsistencies in the fabrication process. This visualization can help
        in identifying areas within the device geometry that may require design
        adjustments to improve fabrication consistency.

        Parameters
        ----------
        show_buffer : bool, optional
            If True, the buffer zones around the device will also be visualized. By
            default, it is set to True.
        ax : Optional[Axes], optional
            An existing matplotlib Axes object to draw the uncertainty visualization on.
            If None, a new figure and axes will be created. Defaults to None.

        Returns
        -------
        Axes
            The matplotlib Axes object containing the uncertainty visualization. This
            can be used for further customization or saving the plot after the method
            returns.
        """
        ax = self._plot_base(show_buffer=show_buffer, ax=ax, **kwargs)

        uncertainty_array = 1 - 2 * np.abs(0.5 - self.device_array)

        _ = ax.imshow(
            uncertainty_array,
            extent=[0, self.device_array.shape[1], 0, self.device_array.shape[0]],
            **kwargs,
        )

        cbar = plt.colorbar(_, ax=ax)
        cbar.set_label("Uncertainty (a.u.)")
        return ax

    def _add_buffer_visualization(self, ax):
        buffer_thickness = self.buffer_spec.thickness
        buffer_fill = (0, 1, 0, 0.2)
        buffer_hatch = "/"

        mid_rect = Rectangle(
            (buffer_thickness, buffer_thickness),
            self.device_array.shape[1] - 2 * buffer_thickness,
            self.device_array.shape[0] - 2 * buffer_thickness,
            facecolor="none",
            edgecolor="black",
            linewidth=1,
        )
        ax.add_patch(mid_rect)

        top_rect = Rectangle(
            (0, 0),
            self.device_array.shape[1],
            buffer_thickness,
            facecolor=buffer_fill,
            hatch=buffer_hatch,
        )
        ax.add_patch(top_rect)

        bottom_rect = Rectangle(
            (0, self.device_array.shape[0] - buffer_thickness),
            self.device_array.shape[1],
            buffer_thickness,
            facecolor=buffer_fill,
            hatch=buffer_hatch,
        )
        ax.add_patch(bottom_rect)

        left_rect = Rectangle(
            (0, buffer_thickness),
            buffer_thickness,
            self.device_array.shape[0] - 2 * buffer_thickness,
            facecolor=buffer_fill,
            hatch=buffer_hatch,
        )
        ax.add_patch(left_rect)

        right_rect = Rectangle(
            (
                self.device_array.shape[1] - buffer_thickness,
                buffer_thickness,
            ),
            buffer_thickness,
            self.device_array.shape[0] - 2 * buffer_thickness,
            facecolor=buffer_fill,
            hatch=buffer_hatch,
        )
        ax.add_patch(right_rect)

    def normalize(self) -> "Device":
        """
        Normalize the device geometry.

        Returns
        -------
        Device
            A new instance of the Device with the normalized geometry.
        """
        normalized_device_array = geometry.normalize(device_array=self.device_array)
        return self.model_copy(update={"device_array": normalized_device_array})

    def binarize(self, eta: float = 0.5, beta: float = np.inf) -> "Device":
        """
        Binarize the device geometry based on a threshold and a scaling factor.

        Parameters
        ----------
        eta : float, optional
            The threshold value for binarization. Defaults to 0.5.
        beta : float, optional
            The scaling factor for the binarization process. A higher value makes the
            transition sharper. Defaults to np.inf, which results in a hard threshold.

        Returns
        -------
        Device
            A new instance of the Device with the binarized geometry.
        """
        binarized_device_array = geometry.binarize(
            device_array=self.device_array, eta=eta, beta=beta
        )
        return self.model_copy(update={"device_array": binarized_device_array})

    def binarize_hard(self, eta: float = 0.5) -> "Device":
        """
        Apply a hard threshold to binarize the device geometry.

        Parameters
        ----------
        eta : float, optional
            The threshold value for binarization. Defaults to 0.5.

        Returns
        -------
        Device
            A new instance of the Device with the threshold-binarized geometry.
        """
        binarized_device_array = geometry.binarize_hard(
            device_array=self.device_array, eta=eta
        )
        return self.model_copy(update={"device_array": binarized_device_array})

    def binarize_monte_carlo(
        self,
        threshold_noise_std: float = 2.0,
        threshold_blur_std: float = 9.0,
    ) -> "Device":
        """
        Binarize the device geometry using a Monte Carlo approach with Gaussian
        blurring.

        This method applies a dynamic thresholding technique where the threshold value
        is determined by a base value perturbed by Gaussian-distributed random noise.
        The threshold is then spatially varied across the device array using Gaussian
        blurring, simulating a more realistic scenario where the threshold is not
        uniform across the device.

        Parameters
        ----------
        threshold_noise_std : float, optional
            The standard deviation of the Gaussian distribution used to generate noise
            for the threshold values. This controls the amount of randomness in the
            threshold. Defaults to 2.0.
        threshold_blur_std : float, optional
            The standard deviation for the Gaussian kernel used in blurring the
            threshold map. This controls the spatial variation of the threshold across
            the array. Defaults to 9.0.

        Returns
        -------
        Device
            A new instance of the Device with the binarized geometry.
        """
        binarized_device_array = geometry.binarize_monte_carlo(
            device_array=self.device_array,
            threshold_noise_std=threshold_noise_std,
            threshold_blur_std=threshold_blur_std,
        )
        return self.model_copy(update={"device_array": binarized_device_array})

    def ternarize(self, eta1: float = 1 / 3, eta2: float = 2 / 3) -> "Device":
        """
        Ternarize the device geometry based on two thresholds.

        Parameters
        ----------
        eta1 : float, optional
            The first threshold value for ternarization. Defaults to 1/3.
        eta2 : float, optional
            The second threshold value for ternarization. Defaults to 2/3.

        Returns
        -------
        Device
            A new instance of the Device with the ternarized geometry.
        """
        ternarized_device_array = geometry.ternarize(
            device_array=self.device_array, eta1=eta1, eta2=eta2
        )
        return self.model_copy(update={"device_array": ternarized_device_array})

    def trim(self) -> "Device":
        """
        Trim the device geometry by removing empty space around it.

        Parameters
        ----------
        buffer_thickness : int, optional
            The thickness of the buffer to leave around the empty space. Defaults to 0,
            which means no buffer is added.

        Returns
        -------
        Device
            A new instance of the Device with the trimmed geometry.
        """
        trimmed_device_array = geometry.trim(
            device_array=self.device_array,
            buffer_thickness=self.buffer_spec.thickness,
        )
        return self.model_copy(update={"device_array": trimmed_device_array})

    def blur(self, sigma: float = 1.0) -> "Device":
        """
        Apply Gaussian blur to the device geometry and normalize the result.

        Parameters
        ----------
        sigma : float, optional
            The standard deviation for the Gaussian kernel. This controls the amount of
            blurring. Defaults to 1.0.

        Returns
        -------
        Device
            A new instance of the Device with the blurred and normalized geometry.
        """
        blurred_device_array = geometry.blur(
            device_array=self.device_array, sigma=sigma
        )
        return self.model_copy(update={"device_array": blurred_device_array})

    def rotate(self, angle: float) -> "Device":
        """
        Rotate the device geometry by a given angle.

        Parameters
        ----------
        angle : float
            The angle of rotation in degrees. Positive values mean counter-clockwise
            rotation.

        Returns
        -------
        Device
            A new instance of the Device with the rotated geometry.
        """
        rotated_device_array = geometry.rotate(
            device_array=self.device_array, angle=angle
        )
        return self.model_copy(update={"device_array": rotated_device_array})

    def erode(self, kernel_size: int = 3) -> "Device":
        """
        Erode the device geometry by removing small areas of overlap.

        Parameters
        ----------
        kernel_size : int
            The size of the kernel used for erosion.

        Returns
        -------
        Device
            A new instance of the Device with the eroded geometry.
        """
        eroded_device_array = geometry.erode(
            device_array=self.device_array, kernel_size=kernel_size
        )
        return self.model_copy(update={"device_array": eroded_device_array})

    def dilate(self, kernel_size: int = 3) -> "Device":
        """
        Dilate the device geometry by expanding areas of overlap.

        Parameters
        ----------
        kernel_size : int
            The size of the kernel used for dilation.

        Returns
        -------
        Device
            A new instance of the Device with the dilated geometry.
        """
        dilated_device_array = geometry.dilate(
            device_array=self.device_array, kernel_size=kernel_size
        )
        return self.model_copy(update={"device_array": dilated_device_array})
