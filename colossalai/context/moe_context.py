import torch
import torch.distributed as dist
from .parallel_mode import ParallelMode


def _check_sanity():
    from colossalai.core import global_context as gpc
    if gpc.tensor_parallel_size > 1 or gpc.pipeline_parallel_size > 1:
        raise NotImplementedError("Moe is not compatible with tensor or "
                                  "pipeline parallel at present.")


class MoeInfo:
    """Moe parallelism information, storing parallel sizes and groups.
    """

    def __init__(self, ep_size: int, dp_size: int):
        _check_sanity()
        self.ep_size = ep_size
        self.dp_size = dp_size
        self.ep_group = None
        # data parallel group for experts, since ep_group is different
        # we may have different dp_group from get_group(ParallelMode.DATA)
        self.dp_group = None

        # Here we assume tensor parallel size = 1
        # Otherwise, MoE can't be used
        # Since TENSOR parallel group and DATA parallel group
        # have been created, we can use them directly.
        if ep_size == 1:
            from colossalai.core import global_context as gpc
            self.ep_group = gpc.get_group(ParallelMode.TENSOR)
            self.dp_group = gpc.get_group(ParallelMode.DATA)
            return

        if dp_size == 1:
            from colossalai.core import global_context as gpc
            self.ep_group = gpc.get_group(ParallelMode.DATA)
            self.dp_group = gpc.get_group(ParallelMode.TENSOR)
            return

        rank = dist.get_rank()
        # Create expert parallel group
        for i in range(dp_size):
            ranks = [i * ep_size + j for j in range(ep_size)]
            group = dist.new_group(ranks)
            if rank in ranks:
                self.ep_group = group

        # Create data parallel group
        for j in range(ep_size):
            ranks = [i * ep_size + j for i in range(dp_size)]
            group = dist.new_group(ranks)
            if rank in ranks:
                self.dp_group = group


class MoeContext:
    """MoE parallel context manager. This class manages different
    parallel groups in MoE context and MoE loss in training.
    """
    __instance = None

    @staticmethod
    def get_instance():
        if MoeContext.__instance is None:
            MoeContext.__instance = MoeContext()
        return MoeContext.__instance

    def __init__(self):
        self.world_size = 1
        # Users may want to set maximum expert parallel size smaller than the world size
        # since very low bandwidth across nodes may constrain the performance of MoE
        # When we have a maximum expert parallel size, we have a minimum data parallel size naturally
        self.max_ep_size = 1
        self.min_dp_size = 1
        self.aux_loss = None
        self.use_kernel_optim = True

        self.has_setup = False
        self._info_dict = dict()

    @property
    def information(self):
        return self._info_dict

    @property
    def is_initialized(self):
        return self.has_setup

    def setup(self, seed: int, use_kernel_optim: bool = True):

        assert not self.is_initialized, "MoE distributed context shouldn't be set up again"
        _check_sanity()
        assert torch.cuda.is_available(), "MoE requires to enable CUDA first"

        self.world_size = dist.get_world_size()

        from colossalai.core import global_context as gpc
        self.max_ep_size = gpc.config.get('max_ep_size', self.world_size)
        assert self.world_size % self.max_ep_size == 0, \
            "Maximum epxert parallel size must be a factor of the number of GPUs"
        self.min_dp_size = self.world_size // self.max_ep_size

        # Enabling kernel optimization may raise error in some cases
        # Users can close kernel optimization manually
        self.use_kernel_optim = use_kernel_optim

        from .random import moe_set_seed
        moe_set_seed(seed)
        self.has_setup = True

    def get_info(self, num_experts: int):
        """Automatically deploys experts and returns parallel infomation about
        distributed communication groups.
        """

        gt_flag = num_experts % self.max_ep_size == 0    # check whether num_experts is greater
        lt_flag = self.max_ep_size % num_experts == 0    # check whether num_experts is less

        assert gt_flag or lt_flag, "Automatic experts placement do not support such situation right now."

        # If the number of experts is greater than maximum expert parallel size,
        # there are multiple experts in each GPU and each GPU has different experts
        # So it's data parallel size is 1
        # Otherwise, there is only one expert in each GPU
        # The data parallel size should be calculated
        dp_size = 1 if gt_flag else self.max_ep_size // num_experts
        ep_size = self.max_ep_size // dp_size

        # Calculate the number of experts for each GPU
        num_local_experts = 1 if lt_flag else num_experts // self.max_ep_size

        # Don't forget to multiply minimum data parallel size
        dp_size *= self.min_dp_size
        if not (ep_size in self.information):
            self.information[ep_size] = MoeInfo(ep_size, dp_size)

        return num_local_experts, self.information[ep_size]

    def set_kernel_not_use(self):
        self.use_kernel_optim = False

    def reset_loss(self):
        self.aux_loss = 0

    def add_loss(self, loss):
        self.aux_loss += loss

    def get_loss(self):
        return self.aux_loss
