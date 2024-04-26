"""Provides functions for manipulating numpy arrays of device geometries."""

import cv2
import numpy as np


def normalize(device_array: np.ndarray) -> np.ndarray:
    """
    Normalize the input numpy array to have values between 0 and 1.

    Parameters
    ----------
    device_array : np.ndarray
        The input array to be normalized.

    Returns
    -------
    np.ndarray
        The normalized array with values scaled between 0 and 1.
    """
    return (device_array - np.min(device_array)) / (
        np.max(device_array) - np.min(device_array)
    )


def binarize(
    device_array: np.ndarray, eta: float = 0.5, beta: float = np.inf
) -> np.ndarray:
    """
    Binarize the input numpy array based on a threshold and a scaling factor.

    Parameters
    ----------
    device_array : np.ndarray
        The input array to be binarized.
    eta : float, optional
        The threshold value for binarization. Defaults to 0.5.
    beta : float, optional
        The scaling factor for the binarization process. A higher value makes the
        transition sharper. Defaults to np.inf, which results in a hard threshold.

    Returns
    -------
    np.ndarray
        The binarized array with elements scaled to 0 or 1.
    """
    return (np.tanh(beta * eta) + np.tanh(beta * (device_array - eta))) / (
        np.tanh(beta * eta) + np.tanh(beta * (1 - eta))
    )


def binarize_hard(device_array: np.ndarray, eta: float = 0.5) -> np.ndarray:
    """
    Apply a hard threshold to binarize the input numpy array.

    Parameters
    ----------
    device_array : np.ndarray
        The input array to be binarized.
    eta : float, optional
        The threshold value for binarization. Defaults to 0.5.

    Returns
    -------
    np.ndarray
        The binarized array with elements set to 0 or 1 based on the threshold.
    """
    return np.where(device_array < eta, 0.0, 1.0)


def binarize_sem(sem_array: np.ndarray) -> np.ndarray:
    """
    Binarize a grayscale SEM (Scanning Electron Microscope) image.

    This function applies Otsu's method to automatically determine the optimal threshold
    value for binarization of a grayscale SEM image.

    Parameters
    ----------
    sem_array : np.ndarray
        The input SEM image array to be binarized.

    Returns
    -------
    np.ndarray
        The binarized SEM image array with elements scaled to 0 or 1.
    """
    return cv2.threshold(
        sem_array.astype("uint8"), 0, 1, cv2.THRESH_BINARY + cv2.THRESH_OTSU
    )[1]


def binarize_monte_carlo(
    device_array: np.ndarray,
    threshold_noise_std: float,
    threshold_blur_std: float,
) -> np.ndarray:
    """
    Binarize the input numpy array using a Monte Carlo approach with Gaussian blurring.

    This function applies a dynamic thresholding technique where the threshold value is
    determined by a base value perturbed by Gaussian-distributed random noise. The
    threshold is then spatially varied across the array using Gaussian blurring,
    simulating a more realistic scenario where the threshold is not uniform across the
    device.

    Parameters
    ----------
    device_array : np.ndarray
        The input array to be binarized.
    threshold_noise_std : float
        The standard deviation of the Gaussian distribution used to generate noise for
        the threshold values. This controls the amount of randomness in the threshold.
    threshold_blur_std : float
        The standard deviation for the Gaussian kernel used in blurring the threshold
        map. This controls the spatial variation of the threshold across the array.

    Returns
    -------
    np.ndarray
        The binarized array with elements set to 0 or 1 based on the dynamically
        generated threshold.
    """
    base_threshold = np.clip(np.random.normal(loc=0.5, scale=0.5 / 2), 0.4, 0.6)
    threshold_noise = np.random.normal(
        loc=0, scale=threshold_noise_std, size=device_array.shape
    )
    spatial_threshold = cv2.GaussianBlur(
        threshold_noise, ksize=(0, 0), sigmaX=threshold_blur_std
    )
    dynamic_threshold = base_threshold + spatial_threshold
    return np.where(device_array < dynamic_threshold, 0.0, 1.0)


def ternarize(
    device_array: np.ndarray, eta1: float = 1 / 3, eta2: float = 2 / 3
) -> np.ndarray:
    """
    Ternarize the input numpy array based on two thresholds.

    Parameters
    ----------
    device_array : np.ndarray
        The input array to be ternarized.
    eta1 : float, optional
        The first threshold value for ternarization. Defaults to 1/3.
    eta2 : float, optional
        The second threshold value for ternarization. Defaults to 2/3.

    Returns
    -------
    np.ndarray
        The ternarized array with elements set to 0, 0.5, or 1 based on the thresholds.
    """
    return np.where(device_array < eta1, 0.0, np.where(device_array >= eta2, 1.0, 0.5))


def trim(device_array: np.ndarray, buffer_thickness: int = 0) -> np.ndarray:
    """
    Trim the input numpy array by removing rows and columns that are completely zero.

    Parameters
    ----------
    device_array : np.ndarray
        The input array to be trimmed.
    buffer_thickness : int, optional
        The thickness of the buffer to leave around the non-zero elements of the array.
        Defaults to 0, which means no buffer is added.

    Returns
    -------
    np.ndarray
        The trimmed array, potentially with a buffer around the non-zero elements.
    """
    nonzero_rows, nonzero_cols = np.nonzero(device_array)
    row_min = max(nonzero_rows.min() - buffer_thickness, 0)
    row_max = min(
        nonzero_rows.max() + buffer_thickness + 1,
        device_array.shape[0],
    )
    col_min = max(nonzero_cols.min() - buffer_thickness, 0)
    col_max = min(
        nonzero_cols.max() + buffer_thickness + 1,
        device_array.shape[1],
    )
    return device_array[
        row_min:row_max,
        col_min:col_max,
    ]


def blur(device_array: np.ndarray, sigma: float = 1.0) -> np.ndarray:
    """
    Apply Gaussian blur to the input numpy array and normalize the result.

    Parameters
    ----------
    device_array : np.ndarray
        The input array to be blurred.
    sigma : float, optional
        The standard deviation for the Gaussian kernel. This controls the amount of
        blurring. Defaults to 1.0.

    Returns
    -------
    np.ndarray
        The blurred and normalized array with values scaled between 0 and 1.
    """
    return normalize(cv2.GaussianBlur(device_array, ksize=(0, 0), sigmaX=sigma))


def rotate(device_array: np.ndarray, angle: float) -> np.ndarray:
    """
    Rotate the input numpy array by a given angle.

    Parameters
    ----------
    device_array : np.ndarray
        The input array to be rotated.
    angle : float
        The angle of rotation in degrees. Positive values mean counter-clockwise
        rotation.

    Returns
    -------
    np.ndarray
        The rotated array.
    """
    center = (device_array.shape[1] / 2, device_array.shape[0] / 2)
    rotation_matrix = cv2.getRotationMatrix2D(center=center, angle=angle, scale=1)
    return cv2.warpAffine(
        device_array,
        M=rotation_matrix,
        dsize=(device_array.shape[1], device_array.shape[0]),
    )


def erode(device_array: np.ndarray, kernel_size: int) -> np.ndarray:
    """
    Erode the input numpy array using a specified kernel size and number of iterations.

    Parameters
    ----------
    device_array : np.ndarray
        The input array representing the device geometry to be eroded.
    kernel_size : int
        The size of the kernel used for erosion.

    Returns
    -------
    np.ndarray
        The eroded array.
    """
    kernel = np.ones((kernel_size, kernel_size), dtype=np.uint8)
    return cv2.erode(device_array, kernel=kernel)


def dilate(device_array: np.ndarray, kernel_size: int) -> np.ndarray:
    """
    Dilate the input numpy array using a specified kernel size.

    Parameters
    ----------
    device_array : np.ndarray
        The input array representing the device geometry to be dilated.
    kernel_size : int
        The size of the kernel used for dilation.

    Returns
    -------
    np.ndarray
        The dilated array.
    """
    kernel = np.ones((kernel_size, kernel_size), dtype=np.uint8)
    return cv2.dilate(device_array, kernel=kernel)


def pad_multiple(
    device_array: np.ndarray, slice_length: int, pad_factor: int
) -> np.ndarray:
    pady = (
        slice_length * np.ceil(device_array.shape[0] / slice_length)
        - device_array.shape[0]
    ) / 2 + slice_length * (pad_factor - 1) / 2
    padx = (
        slice_length * np.ceil(device_array.shape[1] / slice_length)
        - device_array.shape[1]
    ) / 2 + slice_length * (pad_factor - 1) / 2
    device_array = np.pad(
        device_array,
        [
            (int(np.ceil(pady)), int(np.floor(pady))),
            (int(np.ceil(padx)), int(np.floor(padx))),
        ],
        mode="constant",
    )
    return device_array
