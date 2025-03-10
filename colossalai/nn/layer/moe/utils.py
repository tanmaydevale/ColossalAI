import torch
import torch.nn.functional as F
from colossalai.utils import get_current_device
from colossalai.core import MOE_CONTEXT
from .experts import FFNExperts, TPExperts


class NormalNoiseGenerator:
    """Generates a random noisy mask for logtis tensor.

    All noise is generated from a normal distribution (0, 1 / E^2), where
    E = the number of experts.

    :param num_experts: The number of experts
    :type num_experts: int
    """

    def __init__(self, num_experts: int):
        self.normal = torch.distributions.normal.Normal(loc=torch.tensor(0.0, device=get_current_device()),
                                                        scale=torch.tensor(1.0 / num_experts**2,
                                                                           device=get_current_device())).rsample

    def __call__(self, inputs: torch.Tensor):
        noisy = self.normal(inputs.shape)
        return inputs + noisy


class UniformNoiseGenerator:
    """Generates a random noisy mask for logtis tensor.
    copied from mesh tensorflow:
    Multiply values by a random number between 1-epsilon and 1+epsilon.
    Makes models more resilient to rounding errors introduced by bfloat16.
    This seems particularly important for logits.

    :param eps: Epsilon in generator
    :type eps: float
    """

    def __init__(self, eps: float = 1e-2):
        self.uniform = torch.distributions.uniform.Uniform(low=torch.tensor(1.0 - eps, device=get_current_device()),
                                                           high=torch.tensor(1.0 + eps,
                                                                             device=get_current_device())).rsample

    def __call__(self, inputs: torch.Tensor):
        noisy = self.uniform(inputs.shape)
        return inputs * noisy


def autocast_softmax(inputs: torch.Tensor, dim: int):
    assert inputs.dtype in {torch.float16, torch.float32}
    fp16_flag = (inputs.dtype == torch.float16)
    sm_input = inputs.to(torch.float32) if fp16_flag else inputs
    sm_output = F.softmax(sm_input, dim)
    return sm_output


def build_ffn_experts(num_experts: int, d_model: int, d_ff: int, activation=None, drop_rate: float = 0):
    mep_size = MOE_CONTEXT.max_ep_size
    if num_experts % mep_size == 0 or mep_size % num_experts == 0:
        return FFNExperts(num_experts, d_model, d_ff, activation, drop_rate)
    elif d_ff % mep_size == 0:
        return TPExperts(num_experts, d_model, d_ff, activation, drop_rate)
    else:
        raise NotImplementedError(f"Can not build {num_experts} experts in {mep_size} GPUS.")
