import asyncio
from collections.abc import AsyncIterator
from pathlib import Path, PurePosixPath
from typing import cast

from pymobiledevice3 import usbmux
from pymobiledevice3.exceptions import ConnectionFailedToUsbmuxdError
from pymobiledevice3.lockdown import create_using_usbmux
from pymobiledevice3.services.afc import MAXIMUM_READ_SIZE, AfcService

from dcimport.importer import DirEntry, FileStat


class MultipleDevicesError(Exception):
    """More than one device is connected and no UDID was given to pick one."""

    def __init__(self, udids: list[str]):
        super().__init__(f"Multiple devices connected: {', '.join(udids)}")
        self.udids = udids


class AfcSource:
    """MediaSource implementation backed by an AFC connection to an iPhone.
    Obtain via `afc_connect`; call `close` when done."""

    def __init__(self, afc: AfcService, device_name: str | None = None):
        self._afc = afc
        self.device_name = device_name

    async def list_files(self, path: PurePosixPath) -> AsyncIterator[PurePosixPath]:
        """Recursively list `path` via the AFC `dirlist` API."""

        async for entry in self._afc.dirlist(str(path)):
            yield PurePosixPath(entry)

    async def stat(self, path: PurePosixPath) -> FileStat | DirEntry:
        """Stat `path` via AFC, mapping a directory to `DirEntry`."""

        raw = cast("dict", await self._afc.stat(str(path)))

        # st_ifmt source: https://github.com/doronz88/pymobiledevice3/blob/master/pymobiledevice3/services/afc.py
        if raw.get("st_ifmt") == "S_IFDIR":
            return DirEntry()

        return FileStat(size=raw["st_size"], mtime=raw["st_mtime"])

    async def download(self, path: PurePosixPath, target: Path) -> None:
        """Stream `path` off the device in chunks into local `target`."""

        handle = await self._afc.fopen(str(path), "r")

        try:
            with open(target, "wb") as local_file:
                while True:
                    data = await self._afc.fread(handle, MAXIMUM_READ_SIZE)

                    if not data:
                        break

                    local_file.write(data)
        finally:
            await self._afc.fclose(handle)

    async def close(self):
        # AfcService is an async context manager: __aexit__ tears down the demux
        # reader task that fread/fclose depend on
        await self._afc.__aexit__(None, None, None)


async def afc_connect(udid: str | None = None, retries: int = 3) -> AfcSource:
    """Connect to an iPhone and return an AfcSource. With multiple devices connected,
    `udid` selects which one; without it, exactly one device must be connected.

    Raises:
        MultipleDevicesError: If several devices are connected and `udid` is None.
        ConnectionError: If the connection fails after `retries` attempts (or due to an unknown error).
    """

    _e = None

    for i in range(retries):
        try:
            source = await _connect_once(udid)

        except MultipleDevicesError:
            raise

        except ConnectionFailedToUsbmuxdError as e:
            _e = e
            await asyncio.sleep(2**i)  # exponential backoff

        except Exception as e:
            msg = "Failed to connect to the device due to an unexpected error"
            raise ConnectionError(msg) from e

        else:
            return source

    msg = f"Failed to connect to the device after {retries} attempts"
    raise ConnectionError(msg) from _e


async def _connect_once(udid: str | None) -> AfcSource:
    if udid is None:
        devices = await usbmux.list_devices()
        # a device can show up once per connection type (usb + wifi)
        serials = sorted({device.serial for device in devices})

        if len(serials) > 1:
            raise MultipleDevicesError(udids=serials)

    # upstream annotates `serial: str` but its own default is None (= any device)
    lockdown = await create_using_usbmux(serial=cast("str", udid), autopair=True)

    raw_name = await lockdown.get_value(key="DeviceName")
    device_name = raw_name if isinstance(raw_name, str) else lockdown.display_name

    afc = AfcService(lockdown)
    # __aenter__ starts the demux reader task that fread/fclose depend on
    await afc.__aenter__()

    return AfcSource(afc, device_name=device_name)
