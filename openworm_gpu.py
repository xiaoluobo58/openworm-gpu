"""Check that the container can use an NVIDIA GPU through PoCL's CUDA backend."""

import ctypes as ct
import sys


CL_DEVICE_TYPE_GPU = 1 << 2
CL_DEVICE_VENDOR_ID = 0x1001
CL_DEVICE_AVAILABLE = 0x1027
CL_DEVICE_NAME = 0x102B
CL_DEVICE_VERSION = 0x102F
NVIDIA_VENDOR_ID = 0x10DE


def check_gpu():
    try:
        cuda = ct.CDLL("libcuda.so.1")
        opencl = ct.CDLL("libOpenCL.so.1")
    except OSError as error:
        raise RuntimeError(str(error)) from error

    count = ct.c_int()
    status = cuda.cuInit(0)
    if status:
        raise RuntimeError(f"CUDA initialization failed (cuInit returned {status})")
    status = cuda.cuDeviceGetCount(ct.byref(count))
    if status or count.value < 1:
        raise RuntimeError(f"CUDA found no usable GPU (status {status})")

    handle = ct.c_void_p
    uint = ct.c_uint
    opencl.clGetPlatformIDs.argtypes = [uint, ct.POINTER(handle), ct.POINTER(uint)]
    opencl.clGetDeviceIDs.argtypes = [
        handle,
        ct.c_uint64,
        uint,
        ct.POINTER(handle),
        ct.POINTER(uint),
    ]
    opencl.clGetDeviceInfo.argtypes = [
        handle,
        uint,
        ct.c_size_t,
        handle,
        ct.POINTER(ct.c_size_t),
    ]

    platform_count = uint()
    status = opencl.clGetPlatformIDs(0, None, ct.byref(platform_count))
    if status or not platform_count.value:
        raise RuntimeError(f"PoCL exposed no OpenCL platform (status {status})")
    platforms = (handle * platform_count.value)()
    status = opencl.clGetPlatformIDs(len(platforms), platforms, None)
    if status:
        raise RuntimeError(f"OpenCL platform enumeration failed (status {status})")

    for platform in platforms:
        device_count = uint()
        status = opencl.clGetDeviceIDs(
            platform, CL_DEVICE_TYPE_GPU, 0, None, ct.byref(device_count)
        )
        if status or not device_count.value:
            continue
        devices = (handle * device_count.value)()
        if opencl.clGetDeviceIDs(platform, CL_DEVICE_TYPE_GPU, len(devices), devices, None):
            continue
        for device in devices:
            vendor = uint()
            available = uint()
            name = ct.create_string_buffer(256)
            version = ct.create_string_buffer(256)
            fields = (
                (CL_DEVICE_VENDOR_ID, vendor),
                (CL_DEVICE_AVAILABLE, available),
                (CL_DEVICE_NAME, name),
                (CL_DEVICE_VERSION, version),
            )
            if any(
                opencl.clGetDeviceInfo(device, field, ct.sizeof(value), ct.byref(value), None)
                for field, value in fields
            ):
                continue
            if (
                vendor.value == NVIDIA_VENDOR_ID
                and available.value
                and b"PoCL" in version.value
                and b"CUDA-" in version.value
            ):
                return (
                    f"{name.value.decode(errors='replace')} "
                    f"({version.value.decode(errors='replace')})"
                )

    raise RuntimeError("CUDA is available, but PoCL did not expose an NVIDIA OpenCL GPU")


def require_gpu():
    try:
        print(f"OpenWorm GPU preflight passed: {check_gpu()}", flush=True)
    except RuntimeError as error:
        sys.exit(
            f"OpenWorm GPU preflight failed: {error}. "
            "Check Docker GPU passthrough, the WSL 2 backend and the Windows NVIDIA driver."
        )


if __name__ == "__main__":
    require_gpu()
