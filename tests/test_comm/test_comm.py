from functools import partial

import pytest
import torch
import torch.distributed as dist
import torch.multiprocessing as mp
from colossalai.communication import all_gather, all_reduce, reduce_scatter
from colossalai.context import ParallelMode
from colossalai.core import global_context as gpc
from colossalai.initialize import launch
from colossalai.utils import free_port, get_current_device

CONFIG = dict(parallel=dict(data=8, pipeline=1, tensor=dict(mode=None, size=1)))

SIZE = 8


def check_all_gather():
    tensor = torch.tensor([dist.get_rank() * SIZE + j for j in range(SIZE)])
    tensor = tensor.to(get_current_device())
    print('Before:   Rank {0} - {1}'.format(dist.get_rank(), tensor))
    tensor, op = all_gather(tensor, 0, ParallelMode.GLOBAL, async_op=True)
    print('After:    Rank {0} - {1}'.format(dist.get_rank(), tensor))
    op.wait()
    print('Complete: Rank {0} - {1}'.format(dist.get_rank(), tensor))
    torch.cuda.synchronize()


def check_reduce_scatter():
    tensor = torch.tensor([dist.get_rank() * SIZE + j for j in range(SIZE)])
    tensor = tensor.to(get_current_device())
    print('Before:   Rank {0} - {1}'.format(dist.get_rank(), tensor))
    tensor, op = reduce_scatter(tensor, 0, ParallelMode.GLOBAL, async_op=True)
    print('After:    Rank {0} - {1}'.format(dist.get_rank(), tensor))
    op.wait()
    print('Complete: Rank {0} - {1}'.format(dist.get_rank(), tensor))
    torch.cuda.synchronize()


def check_all_reduce():
    tensor = torch.tensor([dist.get_rank() * SIZE + j for j in range(SIZE)])
    tensor = tensor.to(get_current_device())
    print('Before:   Rank {0} - {1}'.format(dist.get_rank(), tensor))
    tensor, op = all_reduce(tensor, ParallelMode.GLOBAL, async_op=True)
    print('After:    Rank {0} - {1}'.format(dist.get_rank(), tensor))
    op.wait()
    print('Complete: Rank {0} - {1}'.format(dist.get_rank(), tensor))
    torch.cuda.synchronize()


def check_layer(rank, world_size, port):
    launch(config=CONFIG, rank=rank, world_size=world_size, host='localhost', port=port, backend='nccl')

    assert dist.get_rank() == gpc.get_global_rank()
    print('Rank {} / {}'.format(dist.get_rank(), dist.get_world_size()))

    check_all_gather()
    check_reduce_scatter()
    check_all_reduce()

    gpc.destroy()
    torch.cuda.empty_cache()


@pytest.mark.dist
def test_comm():
    world_size = 4
    run_func = partial(check_layer, world_size=world_size, port=free_port())
    mp.spawn(run_func, nprocs=world_size)


if __name__ == '__main__':
    test_comm()
