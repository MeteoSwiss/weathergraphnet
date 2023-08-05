import torch
from _typeshed import Incomplete
from torch import nn
from torch.optim import Adam
from torch.optim.lr_scheduler import CyclicLR, StepLR
from typing import List, Optional, Tuple, Union
from weathergraphnet.logger import setup_logger as setup_logger
from weathergraphnet.utils import MyDataset as MyDataset

logger: Incomplete

class TrainingConfigCNN(dict):
    dataset: MyDataset
    optimizer: Union[torch.optim.Optimizer, Adam]
    scheduler: Union[CyclicLR, StepLR]
    loss_fn: Union[nn.Module, nn.MSELoss]
    batch_size: int
    mask: Optional[torch.Tensor]
    epochs: int
    device: str
    seed: int
    def __init__(self, dataset, optimizer, scheduler, loss_fn, batch_size, mask, epochs, device, seed) -> None: ...

class EvaluationConfigCNN(dict):
    dataset: MyDataset
    loss_fn: Union[nn.Module, nn.MSELoss]
    batch_size: int
    mask: Optional[torch.Tensor]
    device: str
    seed: int
    def __init__(self, dataset, loss_fn, batch_size, mask, device, seed) -> None: ...

class BaseNet(nn.Module):
    activation: Incomplete
    channels_in: Incomplete
    channels_out: Incomplete
    hidden_size: Incomplete
    conv_layers: Incomplete
    conv_transposed_layers: Incomplete
    batch_norm_layers: Incomplete
    maxpool: Incomplete
    upsample: Incomplete
    def __init__(self, channels_in: int, channels_out: int, hidden_size: int) -> None: ...
    def forward(self, x) -> None: ...

class Encoder(BaseNet):
    def __init__(self, channels_in: int, channels_out: int, hidden_size: int) -> None: ...
    def forward(self, x): ...

class Decoder(BaseNet):
    def __init__(self, channels_in: int, channels_out: int, hidden_size: int) -> None: ...
    def crop(self, encoder_layer: torch.Tensor, decoder_layer: torch.Tensor) -> torch.Tensor: ...
    def forward(self, x): ...

class UNet(BaseNet):
    encoder: Incomplete
    decoder: Incomplete
    def __init__(self, channels_in: int, channels_out: int, hidden_size: int) -> None: ...
    def forward(self, x): ...
    def train_with_configs(self, rank, configs_train_cnn: TrainingConfigCNN, world_size) -> None: ...
    def eval_with_configs(self, configs_eval_cnn: EvaluationConfigCNN) -> Tuple[float, List[torch.Tensor]]: ...