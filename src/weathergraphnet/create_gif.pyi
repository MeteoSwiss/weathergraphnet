# Standard library
from typing import Callable
from typing import Tuple

# Third-party
import matplotlib.pyplot as plt
import xarray as xr
from _typeshed import Incomplete
from matplotlib import animation
from matplotlib.figure import Figure
from matplotlib.image import AxesImage

# First-party
from weathergraphnet.loggers_configs import setup_logger as setup_logger

logger: Incomplete


def get_member_parts(nc_file: str) -> Tuple[str, ...]: ...
def get_var_min_max(var: xr.DataArray) -> Tuple[float, float]: ...
def open_input_file(input_file: str) -> xr.Dataset: ...
def select_variable(ds: xr.Dataset, var_name: str,
                    member: int) -> xr.DataArray: ...


def plot_first_time_step(var: xr.DataArray, ax: plt.Axes) -> AxesImage: ...
def get_member_name(input_file: str) -> str: ...


def create_update_function(
    im: AxesImage, var: xr.DataArray, member_name: str, var_name: str
) -> Callable[[int], AxesImage]: ...


def create_animation_object(
    fig: Figure, update_func: Callable[[int], AxesImage], num_frames: int
) -> animation.FuncAnimation: ...
def save_animation(ani: animation.FuncAnimation,
                   output_filename: str) -> None: ...


def main(input_file: str, var_name: str, out_dir: str) -> None: ...
