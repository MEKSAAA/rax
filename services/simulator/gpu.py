"""GPU selectors shared by the simulator backends."""
import subprocess


def physical_gpu_index(sim_gpu) -> str:
    """GPU ordinal or UUID -> nvidia-smi (PCI-ordered) index, which renderers enumerate by."""
    selector = str(sim_gpu)
    rows = subprocess.check_output(["nvidia-smi", "--query-gpu=index,uuid", "--format=csv,noheader"],
                                   text=True)
    for row in rows.splitlines():
        index, uuid = (part.strip() for part in row.split(","))
        if selector in (index, uuid):
            return index
    raise ValueError(f"Unknown GPU {selector!r}")
