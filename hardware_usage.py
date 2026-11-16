"""Windows hardware utilization via PDH and DXCore."""

import ctypes as c
import re
import sys
from collections import defaultdict
from ctypes import wintypes as w
from math import isfinite
from uuid import UUID


DEVICES = ("CPU", "GPU", "NPU")
ENGINE = re.compile(r"pid_\d+_luid_(0x[0-9a-f]+_0x[0-9a-f]+)_(phys_\d+_eng_\d+)_engtype_(.+)")
PDH_MORE_DATA = 0x800007D2
PDH_FMT_DOUBLE = 0x200


class CounterValue(c.Structure):
    # The double occupies the same slot as the other values in the PDH union.
    _fields_ = [("status", w.DWORD), ("value", c.c_double)]


class CounterItem(c.Structure):
    _fields_ = [("name", w.LPWSTR), ("value", CounterValue)]


def adapter_loads(samples, adapters, default_kind=None):
    """Combine processes on each engine and use the busiest engine of each kind."""
    totals = defaultdict(float)
    for name, value in samples:
        match = ENGINE.fullmatch(name.lower())
        if not match:
            continue
        luid, engine, engine_type = match.groups()
        kind = adapters.get(luid, default_kind)
        if kind is None and engine_type in {"npu", "neural"}:
            kind = "NPU"
        if kind in ("GPU", "NPU"):
            totals[kind, luid, engine] += value
    busiest = {"GPU": None, "NPU": None}
    for (kind, _, _), load in totals.items():
        busiest[kind] = min(100, max(load, busiest[kind] or 0))
    return busiest


def adapter_ids():
    # DXCore interface IDs and hardware attributes are defined in dxcore_interface.h.
    def guid(value):
        return (c.c_ubyte * 16).from_buffer_copy(UUID(value).bytes_le)

    def method(obj, slot, result, *params):
        table = c.cast(obj, c.POINTER(c.POINTER(c.c_void_p))).contents
        return c.WINFUNCTYPE(result, c.c_void_p, *params)(table[slot])

    def release(obj):
        method(obj, 2, w.ULONG)(obj)

    ptr = c.c_void_p
    try:
        create = c.windll.dxcore.DXCoreCreateAdapterFactory
    except (AttributeError, OSError):
        return {}
    factory = ptr()
    if create(guid("78ee5945-c36e-4b13-a669-005dd11c0f06"), c.byref(factory)) != 0:
        return {}
    adapters = {}
    try:
        adapter_list = method(factory, 3, c.c_long, w.UINT, ptr, ptr, c.POINTER(ptr))
        for kind, attribute in (
            ("GPU", "8c47866b-7583-450d-f0f0-6bada895af4b"),  # graphics fallback
            ("GPU", "b69eb219-3ded-4464-979f-a00bd4687006"),
            ("NPU", "d46140c4-add7-451b-9e56-06fe8c3b58ed"),
        ):
            listing = ptr()
            if adapter_list(factory, 1, guid(attribute), guid("526c7776-40e9-459b-b711-f32ad76dfc28"), c.byref(listing)):
                continue
            try:
                get_adapter = method(listing, 3, c.c_long, w.UINT, ptr, c.POINTER(ptr))
                for index in range(method(listing, 4, w.UINT)(listing)):
                    adapter = ptr()
                    if get_adapter(listing, index, guid("f0db4c7f-fe5a-42a2-bd62-f2a6cf6fc83e"), c.byref(adapter)):
                        continue
                    try:
                        property_value = method(adapter, 6, c.c_long, w.UINT, c.c_size_t, ptr)
                        hardware = c.c_bool()
                        luid = (w.DWORD * 2)()
                        if (not property_value(adapter, 11, c.sizeof(hardware), c.byref(hardware))
                                and hardware.value and not property_value(adapter, 0, c.sizeof(luid), c.byref(luid))):
                            adapters[f"0x{luid[1]:08x}_0x{luid[0]:08x}"] = kind
                    finally:
                        release(adapter)
            finally:
                release(listing)
    finally:
        release(factory)
    return adapters


class HardwareUsage:
    def __init__(self):
        self.query = c.c_void_p()
        self.counters = {}
        self.adapters = {}
        if sys.platform != "win32":
            return
        self.pdh = c.windll.pdh
        if self.pdh.PdhOpenQueryW(None, 0, c.byref(self.query)):
            return
        for key, path in (
            ("CPU", r"\Processor Information(*)\% Processor Time"),
            ("engines", r"\GPU Engine(*)\Utilization Percentage"),
            ("NPU", r"\NPU Engine(*)\Utilization Percentage"),
        ):
            counter = c.c_void_p()
            if not self.pdh.PdhAddEnglishCounterW(self.query, c.c_wchar_p(path), 0, c.byref(counter)):
                self.counters[key] = counter
        self.adapters = adapter_ids()
        self.pdh.PdhCollectQueryData(self.query)

    def read_counter(self, key):
        counter = self.counters.get(key)
        if counter is None:
            return []
        size, count = w.DWORD(), w.DWORD()
        get = self.pdh.PdhGetFormattedCounterArrayW
        status = get(counter, PDH_FMT_DOUBLE, c.byref(size), c.byref(count), None)
        if status & 0xFFFFFFFF != PDH_MORE_DATA or not size.value:
            return []
        buffer = c.create_string_buffer(size.value)
        if get(counter, PDH_FMT_DOUBLE, c.byref(size), c.byref(count), buffer):
            return []
        items = c.cast(buffer, c.POINTER(CounterItem))
        return [
            (item.name, item.value.value)
            for item in items[:count.value]
            if item.name and item.value.status in (0, 1) and isfinite(item.value.value) and item.value.value >= 0
        ]

    def sample(self):
        values = dict.fromkeys(DEVICES)
        if not self.query or self.pdh.PdhCollectQueryData(self.query):
            return values
        values["CPU"] = next((min(100, load) for name, load in self.read_counter("CPU") if name == "_Total"), None)
        values.update(adapter_loads(self.read_counter("engines"), self.adapters))
        dedicated = adapter_loads(self.read_counter("NPU"), {}, "NPU")["NPU"]
        if dedicated is not None:
            values["NPU"] = max(dedicated, values["NPU"] or 0)
        return values

    def close(self):
        if self.query:
            self.pdh.PdhCloseQuery(self.query)
            self.query = c.c_void_p()
