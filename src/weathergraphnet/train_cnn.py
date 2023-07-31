# Standard library
import os
import socket
import warnings
from typing import List

import dask

# Third-party
import matplotlib.pyplot as plt  # type: ignore
import mlflow  # type: ignore
import numpy as np
import torch
import torch.nn.functional as F
import xarray as xr  # type: ignore
from matplotlib import animation  # type: ignore
from pyprojroot import here
from pytorch_lightning.loggers import MLFlowLogger  # type: ignore
from torch import nn
from torch import optim
from torch.distributions import Normal
from torch.optim.lr_scheduler import StepLR
from torch.utils.data import DataLoader
from torch.utils.data import Dataset


class UNet(nn.Module):
    """
    Implementation of the U-Net architecture for image segmentation.

    Args:
        None

    Attributes:
        conv1 (nn.Conv2d): First convolutional layer.
        pool1 (nn.MaxPool2d): First max pooling layer.
        conv2 (nn.Conv2d): Second convolutional layer.
        pool2 (nn.MaxPool2d): Second max pooling layer.
        conv3 (nn.Conv2d): Third convolutional layer.
        pool3 (nn.MaxPool2d): Third max pooling layer.
        conv4 (nn.Conv2d): Fourth convolutional layer.
        pool4 (nn.MaxPool2d): Fourth max pooling layer.
        upconv1 (nn.ConvTranspose2d): First transpose convolutional layer.
        conv5 (nn.Conv2d): Fifth convolutional layer.
        upconv2 (nn.ConvTranspose2d): Second transpose convolutional layer.
        conv6 (nn.Conv2d): Sixth convolutional layer.
        upconv3 (nn.ConvTranspose2d): Third transpose convolutional layer.
        conv7 (nn.Conv2d): Seventh convolutional layer.
        skip1 (nn.Conv2d): First skip connection.
        skip2 (nn.Conv2d): Second skip connection.
        skip3 (nn.Conv2d): Third skip connection.
        outconv (nn.Conv2d): Output convolutional layer.
        activation (nn.ReLU): Activation function.

    Methods:
        forward(x): Performs a forward pass through the network. crop(encoder_layer,
        decoder_layer): Crops the encoder layer to match the size of the decoder layer.
        train_model(dataloader, optimizer, loss_fn, num_epochs): Trains the model on the
        given data. evaluate(dataloader, loss_fn, return_predictions=False): Evaluates
        the model on the given data.
    """

    def __init__(self, channels_in=1, channels_out=1):
        super(UNet, self).__init__()

        # Define the encoder layers
        self.conv1 = nn.Conv2d(channels_in, 64, kernel_size=3, padding=1)
        self.pool1 = nn.MaxPool2d(kernel_size=2, stride=2)
        self.conv2 = nn.Conv2d(64, 128, kernel_size=3, padding=1)
        self.pool2 = nn.MaxPool2d(kernel_size=2, stride=2)
        self.conv3 = nn.Conv2d(128, 256, kernel_size=3, padding=1)
        self.pool3 = nn.MaxPool2d(kernel_size=2, stride=2)
        self.conv4 = nn.Conv2d(256, 512, kernel_size=3, padding=1)
        self.pool4 = nn.MaxPool2d(kernel_size=2, stride=2)

        # Define the decoder layers
        self.upconv1 = nn.ConvTranspose2d(512, 256, kernel_size=2, stride=2)
        self.conv5 = nn.Conv2d(512, 256, kernel_size=3, padding=1)
        self.upconv2 = nn.ConvTranspose2d(256, 128, kernel_size=2, stride=2)
        self.conv6 = nn.Conv2d(256, 128, kernel_size=3, padding=1)
        self.upconv3 = nn.ConvTranspose2d(128, 64, kernel_size=2, stride=2)
        self.conv7 = nn.Conv2d(128, 64, kernel_size=3, padding=1)

        # Define the skip connections
        self.skip1 = nn.Conv2d(256, 256, kernel_size=1)
        self.skip2 = nn.Conv2d(128, 128, kernel_size=1)
        self.skip3 = nn.Conv2d(64, 64, kernel_size=1)

        # Define the output layer
        self.outconv = nn.Conv2d(64, channels_out, kernel_size=1)

        # Define the activation function
        self.activation = nn.ReLU()

    def forward(self, x):
        # Encoder
        x1 = self.activation(self.conv1(x))
        x2 = self.activation(self.pool1(self.conv2(x1)))
        x3 = self.activation(self.pool2(self.conv3(x2)))
        x4 = self.activation(self.pool3(self.conv4(x3)))

        # Decoder
        cropped = 0
        y1 = self.activation(self.upconv1(x4))
        if y1.shape != x3.shape:
            x3 = self.crop(x3, y1)
            cropped += 1
        y1 = self.activation(self.conv5(torch.cat([x3, y1], dim=1)))
        y2 = self.activation(self.upconv2(y1))
        if y2.shape != x2.shape:
            x2 = self.crop(x2, y2)
            cropped += 1
        y2 = self.activation(self.conv6(torch.cat([x2, y2], dim=1)))
        y3 = self.activation(self.upconv3(y2))
        if y3.shape != x1.shape:
            x1 = self.crop(x1, y3)
            cropped += 1
        y3 = self.activation(self.conv7(torch.cat([x1, y3], dim=1)))

        # Output
        out = self.outconv(y3)
        out = F.pad(out, (cropped, 0, 0, 0), mode='replicate')

        return out

    def crop(self, encoder_layer, decoder_layer):
        # Calculate the difference in size between the encoder and decoder layers
        diffY = encoder_layer.size()[2] - decoder_layer.size()[2]
        diffX = encoder_layer.size()[3] - decoder_layer.size()[3]

        # Crop the encoder layer to match the size of the decoder layer
        encoder_layer = encoder_layer[:, :, diffY //
                                      2: encoder_layer.size()[2] -
                                      diffY //
                                      2, diffX //
                                      2: encoder_layer.size()[3] -
                                      diffX //
                                      2]
        if diffX % 2 == 1:
            encoder_layer = encoder_layer[:, :, :, 1:encoder_layer.size()[3]]
        if diffY % 2 == 1:
            encoder_layer = encoder_layer[:, :, 1:encoder_layer.size()[2], :]
        return encoder_layer

    def train_model(self, dataloader, optimizer, scheduler, loss_fn,
                    mask=None, num_epochs=10, device="cuda"):
        best_loss = float('inf')
        for epoch in range(num_epochs):
            running_loss = 0.0
            for i, (input_data, target_data) in enumerate(dataloader):
                input_data = input_data.to(device)
                target_data = target_data.to(device)
                optimizer.zero_grad()
                output = self(input_data)
                if mask is not None:
                    loss = loss_fn(output, target_data, mask.to(device))
                else:
                    loss = loss_fn(output, target_data)
                loss.backward()
                optimizer.step()
                if scheduler is not None:
                    scheduler.step()   # update the learning rate
                running_loss += loss.item()
                avg_loss = running_loss / len(output)
                print(f"Epoch {epoch + 1}: {avg_loss}")
                mlflow.log_metric("loss", avg_loss)
            if avg_loss is not None and avg_loss < best_loss:
                best_loss = avg_loss
                mlflow.pytorch.log_model(self, "models")

    def evaluate(self, dataloader, loss_fn, mask=None,
                 return_predictions=False, device="cuda"):
        self.eval()
        with torch.no_grad():
            loss = 0.0
            if return_predictions:
                y_preds = []
            for i, (input_data, target_data) in enumerate(dataloader):
                input_data = input_data.to(device)
                target_data = target_data.to(device)
                output = self(input_data)
                if mask is not None:
                    loss += loss_fn(output, target_data, mask.to(device))
                else:
                    loss += loss_fn(output, target_data)
                if return_predictions:
                    y_preds.append(output.cpu())
            loss /= len(dataloader)
            if return_predictions:
                return loss, y_preds
            else:
                return loss


class MyDataset(Dataset):
    def __init__(self, data, split):
        self.data = data
        self.split = split

        # Get the number of members in the dataset
        num_members = self.data.sizes["member"]

        # Get the indices of the members
        member_indices = np.arange(num_members)

        # Shuffle the member indices
        np.random.shuffle(member_indices)

        # Split the member indices into train and test sets
        self.train_indices = member_indices[:self.split]
        self.test_indices = member_indices[self.split:]

    def __len__(self):
        return len(self.data.time)

    def __getitem__(self, idx):
        # Get the data for the train and test sets
        x = self.data.isel(member=self.train_indices, time=idx).values
        y = self.data.isel(member=self.test_indices, time=idx).values

        # If x and y are 2D arrays, add a new dimension
        if x.ndim == 2:
            x = np.expand_dims(x, axis=0)
            y = np.expand_dims(y, axis=0)

        return torch.from_numpy(x), torch.from_numpy(y)


class CRPSLoss(nn.Module):
    def __init__(self):
        super().__init__()

    def forward(self, outputs, target):
        # Calculate the mean and standard deviation of the predicted distribution
        mu = torch.mean(outputs, dim=1)  # Mean over ensemble members
        sigma = torch.std(outputs, dim=1) + 1e-6  # Stddev over ensemble members

        # Create a normal distribution with the predicted mean and standard deviation
        dist = Normal(mu, sigma)

        # Calculate the CRPS loss for each sample in the batch
        # Mean over ensemble members and spatial locations
        crps_loss = torch.mean((dist.cdf(target) - 0.5) ** 2, dim=[1, 2, 3])

        return crps_loss


class EnsembleVarianceRegularizationLoss(nn.Module):
    def __init__(self, alpha=0.1):
        super().__init__()
        self.alpha = alpha  # Regularization strength

    def forward(self, outputs, target):
        l1_loss = torch.mean(torch.abs(outputs - target))
        ensemble_variance = torch.var(outputs, dim=1)
        regularization_loss = -self.alpha * torch.mean(ensemble_variance)
        return l1_loss + regularization_loss


class MaskedLoss(nn.Module):
    def __init__(self, loss_fn):
        super().__init__()
        self.loss_fn = loss_fn

    def forward(self, outputs, target, mask):

        # Calculate the loss for each sample in the batch using the specified loss
        # function
        loss = self.loss_fn(outputs, target)

        # Mask the loss for cells where the values stay constant over all observed times
        masked_loss = loss * mask

        # Calculate the mean loss over all unmasked cells
        mean_loss = torch.sum(masked_loss) / torch.sum(mask)

        return mean_loss


def animate(data, member=0, preds="CNN"):
    """Animate the prediction evolution."""
    # Create a new figure object
    fig, ax = plt.subplots()

    # Calculate the 5% and 95% percentile of the y_mem data
    vmin, vmax = np.percentile(y_mem.values, [1, 99])
    # Create a colormap with grey for values outside of the range
    cmap = plt.cm.RdBu_r
    cmap.set_bad(color='grey')

    im = y_mem.isel(time=0).plot(ax=ax, cmap=cmap, vmin=vmin, vmax=vmax)

    plt.gca().invert_yaxis()

    text = ax.text(
        0.5,
        1.05,
        "Theta_v - Time: 0 s\n Member: 0 - None",
        ha='center',
        va='bottom',
        transform=ax.transAxes,
        fontsize=12)
    plt.tight_layout()
    ax.set_title("")  # Remove the plt.title

    def update(frame):
        """Update the data of the current plot."""
        time_in_seconds = round(
            (data.time[frame] - data.time[0]).item() * 24 * 3600
        )
        im.set_array(data.isel(time=frame))
        title = f"Var: Theta_v - Time: {time_in_seconds:.0f} s\n Member: {member} - {preds}"
        text.set_text(title)
        return im, text

    ani = animation.FuncAnimation(
        fig, update, frames=range(len(data.time)), interval=50, blit=True
    )
    return ani


def downscale_data(data, factor):
    """Downscale the data by the given factor.

    Args:
        data (xarray.Dataset): The data to downscale.
        factor (int): The factor by which to downscale the data.

        Returns:
            The downscaled data.

    """
    with dask.config.set(**{'array.slicing.split_large_chunks': False}):
        # Coarsen the height and ncells dimensions by the given factor
        data_coarse = data.coarsen(height=factor, ncells=factor).mean()
        return data_coarse


if __name__ == "__main__":
    # Suppress the warning message
    warnings.simplefilter("always")
    warnings.filterwarnings("ignore", message="Setuptools is replacing distutils.")
    warnings.filterwarnings(
        "ignore",
        message="Encountered an unexpected error while inferring pip requirements",
    )

    # Load the data
    data_train = (
        xr.open_zarr(str(here()) + "/data/data_train.zarr").to_array().squeeze()
    )
    data_train = data_train.transpose(
        "time",
        "member",
        "height",
        "ncells",
    )

    data_test = xr.open_zarr(str(here()) +
                             "/data/data_test.zarr").to_array().squeeze(drop=False)
    data_test = data_test.transpose(
        "time",
        "member",
        "height",
        "ncells",
    )

    coarsen = True
    if coarsen:
        # Coarsen the data
        data_test = downscale_data(data_test, 4)
        data_train = downscale_data(data_train, 4)

    member_split = 100

    # Create the dataset and dataloader
    dataset = MyDataset(data_train, member_split)
    dataloader = DataLoader(dataset, batch_size=32, shuffle=True)
    dataset_test = MyDataset(data_test, member_split)
    dataloader_test = DataLoader(dataset_test, batch_size=32, shuffle=False)

    model = UNet(
        channels_in=member_split,
        channels_out=data_train.shape[1] -
        member_split)

    optimizer = optim.Adam(model.parameters(), lr=1e-4)
    scheduler = StepLR(optimizer, step_size=3, gamma=0.1)

    # loss_fn = MaskedLoss(loss_fn=nn.L1Loss())
    # loss_fn = nn.L1Loss()
    loss_fn = EnsembleVarianceRegularizationLoss(alpha=0.1)

    if loss_fn == MaskedLoss:
        # Create a mask that masks all cells that stay constant over all time steps
        variance = data_train.var(dim='time')
        # Create a mask that hides all data with zero variance
        mask = variance <= 1e-6
        torch.from_numpy(mask.values.astype(float))
        print(f"Number of masked cells: {(mask[0].values == 1).sum()}", flush=True)

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Using device: {device}")
    # Move the loss function and dataloader to the cuda device
    loss_fn = loss_fn.to(device)
    model.to(device)
    if torch.cuda.device_count() > 1:
        print(f"Using {torch.cuda.device_count()} GPUs")
        model = nn.DataParallel(model)
    train_model = model.module.train_model if isinstance(
        model, nn.DataParallel) else model.train_model

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

    retrain = True
    if retrain:
        # Train the model with MLflow logging
        MLFlowLogger(experiment_name=experiment_name)
        with mlflow.start_run():
            # Train the model
            train_model(
                dataloader,
                optimizer,
                scheduler,
                loss_fn,
                None,
                num_epochs=10,
                device=device,
            )
    else:

        # Load the best checkpoint of the model from the most recent MLflow run
        runs = mlflow.search_runs(
            experiment_names=[experiment_name],
            order_by=["start_time desc"],
            max_results=1)

        if len(runs) == 0:
            print("No runs found in experiment:", experiment_name)
        run_id = runs.iloc[0].run_id
        best_model_path = mlflow.get_artifact_uri()
        best_model_path = os.path.abspath(os.path.join(best_model_path, "../../"))
        best_model_path = os.path.join(
            best_model_path,
            run_id,
            "artifacts",
            "models")
        model = mlflow.pytorch.load_model(best_model_path)
        model.to(device)
        if torch.cuda.device_count() > 1:
            print(f"Using {torch.cuda.device_count()} GPUs")
            model = nn.DataParallel(model)
        evaluate = model.module.evaluate if isinstance(
            model, nn.DataParallel) else model.evaluate
    y_pred: List[torch.Tensor] = []
    test_loss, y_pred = evaluate(
        dataloader_test,
        loss_fn,
        None,
        return_predictions=True,
        device=device,
    )
    print(f"Best model test loss: {test_loss:.4f}")


# Plot the predictions
# pylint: disable=no-member
# type: ignore
y_pred_reshaped = xr.DataArray(
    torch.cat(y_pred).numpy().reshape(
        (data_test.isel(
            member=slice(
                member_split,
                data_test.sizes["member"])).values.shape)),
    dims=[
        "time",
        "member",
        "height",
        "ncells"],
)
member = 0
preds = "CNN"

# Reorder y_mem by the time dimension
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