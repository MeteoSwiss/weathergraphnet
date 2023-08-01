"""Utility functions and classes for the WeatherGraphNet project.

Classes:
    CRPSLoss: Continuous Ranked Probability Score (CRPS) loss function.
    EnsembleVarianceRegularizationLoss: Ensemble variance regularization loss function.
    MaskedLoss: Masked loss function.
    MyDataset: Custom dataset class.

Functions:
    animate: Animate the prediction evolution.
    create_animation: Create an animation of the prediction evolution.
    downscale_data: Downscale the data by the given factor.
    get_runs: Get all runs from the specified experiment.
    load_best_model: Load the best checkpoint of the model from the most recent MLflow
        run.
    load_config_and_data: Load the configuration and data.
    load_data: Load the data.
    setup_mlflow: Setup MLflow.
    suppress_warnings: Suppress all warnings.

"""
# Standard library
import json
import os
import socket
import warnings
from typing import Any
from typing import Dict
from typing import List
from typing import Tuple
from typing import Union

# Third-party
import dask
import matplotlib.pyplot as plt
import mlflow  # type: ignore
import numpy as np
import torch
import xarray as xr
from matplotlib import animation
from matplotlib import cm
from matplotlib.image import AxesImage
from numpy import dtype
from numpy import signedinteger
from pyprojroot import here
from torch import nn
from torch.distributions import Normal
from torch.utils.data import Dataset

# First-party
from weathergraphnet.models import GNNModel
from weathergraphnet.models import UNet


class CRPSLoss(nn.Module):
    """Continuous Ranked Probability Score (CRPS) loss function.

    This class implements the CRPS loss function, which is used to eval the
    performance of probabilistic regression models.

    Args:
        nn.Module: PyTorch module.

    Returns:
        crps_loss: CRPS loss for each sample in the batch.

    """

    def __init__(self) -> None:
        """Initialize the CRPS loss function."""
        super().__init__()

    def forward(self, outputs: Any, target: Any, dim: int = 0) -> Any:
        """Calculate the CRPS loss for each sample in the batch.

        This method calculates the CRPS loss for each sample in the batch using the
        predicted values and target values.

        Args:
            outputs: Predicted values.
            target: Target values.
            dim: Dimension over which to calculate the mean and standard deviation.

        Returns:
            crps_loss: CRPS loss for each sample in the batch.

        """
        # Calculate the mean and standard deviation of the predicted distribution
        mu = torch.mean(outputs, dim=dim)  # Mean over ensemble members
        # Stddev over ensemble members
        sigma = torch.std(outputs, dim=dim) + 1e-6

        # Create a normal distribution with the predicted mean and standard deviation
        dist = Normal(mu, sigma)

        # Calculate the CRPS loss for each sample in the batch Mean over ensemble
        # members and spatial locations
        crps_loss = torch.mean((dist.cdf(target) - 0.5) ** 2, dim=[1, 2, 3])

        return crps_loss


class EnsembleVarianceRegularizationLoss(nn.Module):
    """Ensemble variance regularization loss function.

    This class implements the ensemble variance regularization loss function, which is
    used to improve the performance of probabilistic regression models.

    Args:
        alpha: Regularization strength.

    Returns:
        l1_loss + regularization_loss: Loss for each sample in the batch.

    """

    def __init__(self, alpha: float = 0.1) -> None:
        """Initialize the ensemble variance regularization loss function.

        Args:
            alpha: Regularization strength.

        """
        super().__init__()
        self.alpha = alpha  # Regularization strength

    def forward(self, outputs: Any, target: Any) -> Any:
        """Calculate the loss for each sample using the specified loss function.

        This method calculates the loss for each sample in the batch using the specified
        loss function.

        Args:
            outputs: Predicted values.
            target: Target values.

        Returns:
            l1_loss + regularization_loss: Loss for each sample in the batch.

        """
        l1_loss = torch.mean(torch.abs(outputs - target))
        ensemble_variance = torch.var(outputs, dim=1)
        regularization_loss = -self.alpha * torch.mean(ensemble_variance)
        return l1_loss + regularization_loss


class MaskedLoss(nn.Module):
    """Masked loss function.

    This class implements the masked loss function, which is used to calculate the loss
    for each sample in the batch while ignoring certain cells.

    Args:
        loss_fn: Loss function to use.

    Returns:
        mean_loss: Mean loss over all unmasked cells.

    """

    def __init__(self, loss_fn: nn.Module) -> None:
        """Initialize the masked loss function.

        Args:
            loss_fn: Loss function to use.

        """
        super().__init__()
        self.loss_fn = loss_fn

    def forward(self, outputs: Any, target: Any, mask: Any) -> Any:
        """Calculate the loss for each sample using the specified loss function.

        This method calculates the loss for each sample in the batch using the specified
        loss function, while ignoring certain cells.

        Args:
            outputs: Predicted values.
            target: Target values.
            mask: Mask for cells where the values stay constant over all observed times.

        Returns:
            mean_loss: Mean loss over all unmasked cells.

        """
        # Calculate the loss for each sample in the batch using the specified loss
        # function
        loss = self.loss_fn(outputs, target)

        # Mask the loss for cells where the values stay constant over all observed times
        masked_loss = loss * mask

        # Calculate the mean loss over all unmasked cells
        mean_loss = torch.sum(masked_loss) / torch.sum(mask)

        return mean_loss


class MyDataset(Dataset):
    """Custom dataset class.

    This class implements a custom dataset class, which is used to load and preprocess
    data for training and testing machine learning models.

    Args:
        data: The data to use.
        split: The split between train and test sets.

    Returns:
        x, y: Data for the train and test sets.

    """

    def __init__(self, data: xr.Dataset, split: int):
        """Initialize the custom dataset class.

        Args:
            data: The data to use.
            split: The split between train and test sets.

        """
        self.data: xr.Dataset = data
        self.split = split
        # Get the number of members in the dataset
        num_members: int = self.data.sizes["member"]

        # Get the indices of the members
        member_indices: np.ndarray[Any, dtype[signedinteger[Any]]] = np.arange(
            num_members
        )

        # Shuffle the member indices
        np.random.shuffle(member_indices)

        # Split the member indices into train and test sets
        self.train_indices: np.ndarray = member_indices[: self.split]
        self.test_indices: np.ndarray = member_indices[self.split :]

    def __len__(self) -> int:
        """Get the length of the dataset."""
        return len(self.data.time)

    def __getitem__(self, idx: int) -> Tuple[xr.Dataset, xr.Dataset]:
        """Get the data for the train and test sets.

        This method gets the data for the train and test sets.

        Args:
            idx: Index of the data.

        Returns:
            x, y: Data for the train and test sets.

        """
        # Get the data for the train and test sets
        x: xr.Dataset = self.data.isel(member=self.train_indices, time=idx).unsqueeze(1)
        y: xr.Dataset = self.data.isel(member=self.test_indices, time=idx).unsqueeze(1)

        return x, y

    def __iter__(self):
        """Get the iterator."""
        return iter(self.data.time)


def animate(
    data: xr.Dataset, member: int = 0, preds: str = "CNN"
) -> animation.FuncAnimation:
    """Animate the prediction evolution.

    Args:
        data: The data to animate.
        member: The member to use.
        preds: The predictions to use.

    Returns:
        ani: The animation.

    """
    # Create a new figure object
    fig, ax = plt.subplots()

    # Calculate the 5% and 95% percentile of the y_mem data
    vmin, vmax = np.percentile(np.array(data.values), [1, 99])
    # Create a colormap with grey for values outside of the range
    cmap = cm.get_cmap("RdBu_r")
    cmap.set_bad(color="grey")

    im: AxesImage = data.isel(time=0).plot(ax=ax, cmap=cmap, vmin=vmin, vmax=vmax)

    plt.gca().invert_yaxis()

    text = ax.text(
        0.5,
        1.05,
        "Theta_v - Time: 0 s\n Member: 0 - None",
        ha="center",
        va="bottom",
        transform=ax.transAxes,
        fontsize=12,
    )
    plt.tight_layout()
    ax.set_title("")  # Remove the plt.title

    def update(frame: int) -> Tuple:
        """Update the data of the current plot.

        Args:
            frame: The frame to update.

        Returns:
            im, text: The updated plot.

        """
        time_in_seconds = round((data.time[frame] - data.time[0]).item() * 24 * 3600)
        im.set_array(data.isel(time=frame))
        title = (
            f"Var: Theta_v - Time: {time_in_seconds:.0f} s\n Member: {member} - {preds}"
        )
        text.set_text(title)
        return im, text

    ani = animation.FuncAnimation(
        fig, update, frames=range(len(data.time)), interval=50, blit=True
    )
    return ani


def create_animation(data: dict, member: int, preds: str) -> str:
    """Create an animation of weather data for a given member and prediction type.

    Args:
        data (dict): A dictionary containing the weather data.
        member (int): The member index to plot.
        preds (str): The type of prediction to plot.

    Returns:
        str: The filepath of the output gif.

    """
    y_pred_reshaped = data["y_pred_reshaped"]
    data_test = data["data_test"]
    y_pred_reshaped["time"] = data_test["time"]
    y_pred_reshaped["height"] = data_test["height"]

    # Plot the first time step of the variable
    if preds == "ICON":
        y_mem = data_test.isel(member=member)
    else:
        y_mem = y_pred_reshaped.isel(member=member)

    y_mem = y_mem.sortby(y_mem.time, ascending=True)

    ani = animate(y_mem, member=member, preds=preds)

    # Define the filename for the output gif
    output_filename = f"{here()}/output/animation_member_{member}_{preds}.gif"

    # Save the animation as a gif
    ani.save(output_filename, writer="imagemagick", dpi=100)
    return output_filename


def downscale_data(data: xr.Dataset, factor: int) -> xr.Dataset:
    """Downscale the data by the given factor.

    Args:
        data: The data to downscale.
        factor: The factor by which to downscale the data.

    Returns:
        The downscaled data.

    """
    with dask.config.set(
        Dict[str, bool](**{"array.slicing.split_large_chunks": False})
    ):
        # Coarsen the height and ncells dimensions by the given factor
        data_coarse = data.coarsen(height=factor, ncells=factor).reduce(np.mean)
        return data_coarse


def get_runs(experiment_name: str) -> List[mlflow.entities.Run]:
    """Retrieve a list of runs for a given experiment name.

    Args:
        experiment_name (str): The name of the experiment to retrieve runs for.

    Returns:
        List[mlflow.entities.Run]: A list of runs for the given experiment name.

    Raises:
        ValueError: If no runs are found for the given experiment name.

    """
    runs = mlflow.search_runs(experiment_names=experiment_name)
    if not runs:
        raise ValueError(f"No runs found in experiment: {experiment_name}")
    return runs


def load_best_model(experiment_name: str) -> Union[GNNModel, UNet]:
    """Load the best model from a given MLflow experiment.

    Args:
        experiment_name (str): The name of the MLflow experiment.

    Returns:
        nn.Module: The PyTorch model object.

    """
    runs = get_runs(experiment_name)
    run_id = runs[0]["run_id"]
    best_model_path = mlflow.get_artifact_uri()
    best_model_path = os.path.abspath(os.path.join(best_model_path, "../../"))
    best_model_path = os.path.join(best_model_path, run_id, "artifacts", "models")
    model = mlflow.pytorch.load_model(best_model_path)

    return model


def load_config_and_data() -> Tuple[dict, xr.Dataset, xr.Dataset]:
    """Load configuration and data for the weathergraphnet project.

    Returns:
    Tuple[dict, xr.Dataset, xr.Dataset]: A tuple containing the configuration
        dictionary, and two xarray DataArrays containing the training and testing data.

    """
    with open(
        str(here()) + "/src/weathergraphnet/config.json", "r", encoding="UTF-8"
    ) as f:
        config = json.load(f)

    # Suppress all warnings
    suppress_warnings()

    data_train, data_test = load_data(config)

    if config["coarsen"] > 1:
        # Coarsen the data
        data_test = downscale_data(data_test, config["coarsen"])
        data_train = downscale_data(data_train, config["coarsen"])

    return config, data_train, data_test


def load_data(config: dict) -> Tuple[xr.Dataset, xr.Dataset]:
    """Load training and test data from zarr files and return them as xarray DataArrays.

    Args:
        config (dict): A dictionary containing the paths to the training and test data.

    Returns:
        Tuple[xr.Dataset, xr.Dataset]: A tuple containing the training and test data
        as xarray Dataset.

    """
    # Load the training data
    data_train = xr.open_zarr(str(here()) + config["data_train"]).to_array().squeeze()
    data_train = data_train.transpose(
        "time",
        "member",
        "height",
        "ncells",
    )

    # Load the test data
    data_test = (
        xr.open_zarr(str(here()) + config["data_test"]).to_array().squeeze(drop=False)
    )
    data_test = data_test.transpose(
        "time",
        "member",
        "height",
        "ncells",
    )

    return data_train, data_test


def setup_mlflow() -> Tuple[str, str]:
    """Set up the MLflow experiment and artifact path based on the hostname.

    Returns the artifact path and experiment name as a tuple.

    """
    hostname = socket.gethostname()
    # Set the artifact path based on the hostname
    if "nid" in hostname:
        artifact_path = (
            "/scratch/e1000/meteoswiss/scratch/sadamov/"
            "pyprojects_data/weathergraphnet/mlruns"
        )
        experiment_name = "WGN_balfrin"
    else:
        artifact_path = "/scratch/sadamov/pyprojects_data/weathergraphnet/mlruns"
        experiment_name = "WGN"

    mlflow.set_tracking_uri(str(here()) + "/mlruns")
    existing_experiment = mlflow.get_experiment_by_name(experiment_name)
    if existing_experiment is None:
        mlflow.create_experiment(name=experiment_name, artifact_location=artifact_path)
    mlflow.set_experiment(experiment_name=experiment_name)
    return artifact_path, experiment_name


def suppress_warnings():
    """Suppresses certain warnings that are not relevant to the user."""
    warnings.simplefilter("always")
    warnings.filterwarnings("ignore", message="Setuptools is replacing dist")
    warnings.filterwarnings(
        "ignore",
        message="Encountered an unexpected error while inferring pip requirements",
    )
