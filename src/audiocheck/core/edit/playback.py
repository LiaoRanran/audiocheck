"""播放引擎（路线 P1 柱子）。

为什么单独一层
--------------
编辑工作站要"能听"：播放头跟播、选区试听、A/B 试听。这是路线 §3 盘点里明确"一根都没有"的柱子。
本模块把**可测的传输状态机**与**miniaudio 后端**分开：

* `Transport` —— 纯状态机（stopped/playing/paused），不依赖声卡、不依赖 Qt，
  所有状态转移/播放头推进/选区段/越界都可 headless 测（见 `tests/test_edit_playback.py`）。
* `PlaybackEngine` —— 包 miniaudio，把 `Transport` 的状态映射成真实的 C 层回调；
  `NULL` 后端用于无硬件自动化测试。

实测约束（2026-10-26 探明，写进文档不信记忆）
------------------------------------------------
* miniaudio 在 Windows 的 backends 只有 `WASAPI/DSOUND/WINMM/NULL`，**没有 ASIO**。
  低延迟走 WASAPI（把 `buffersize_msec` 调小即更接近独占）。
* `PlaybackDevice.start(gen)` 的 generator 必须**接收 `framecount`（`.send()`）并 yield
  `bytes`/`array.array`**——miniaudio 在 C 层每帧 `send(framecount)` 要这一帧的样本。
  不按这个契约写会在 cffi 回调里抛 `TypeError`（已踩过）。
* `Devices().get_playbacks()` 返回 `dict` 列表，含 `name` / `id`（cdata，不可 pickle）/
  `formats`（每个是 `{format, samplerate, channels}`）。设备 `id` 不能直接回查出枚举对象，
  所以引擎以"名字"为稳定句柄，open 时再据名字匹配 `id`。

X-run / 热插拔
-------------
miniaudio 的 Python 层不暴露 X-run 计数与设备移除回调（那是 ma_device 的 C 层）。本模块按
"尽力"口径：用 **缓冲水位** 代理 X-run（回调要的数据没在截止前备好 ⇒ 计一次欠载），用
**设备枚举失效/start 抛错**代理热插拔。诚实记账：这不是真 X-run 计数，是下游能拿到的近似。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from enum import Enum
from typing import Callable

import numpy as np

__all__ = [
    "PlayState",
    "Transport",
    "DeviceInfo",
    "enumerate_playback_devices",
    "PlaybackEngine",
    "Backend",
]


class Backend(str, Enum):
    """对外暴露的后端名（只列本项目真能用的；ASIO 不在 miniaudio 里，不列）。"""

    WASAPI = "wasapi"
    DSOUND = "dsound"
    WINMM = "winmm"
    NULL = "null"

    @property
    def ma(self):
        import miniaudio

        return {
            Backend.WASAPI: miniaudio.Backend.WASAPI,
            Backend.DSOUND: miniaudio.Backend.DSOUND,
            Backend.WINMM: miniaudio.Backend.WINMM,
            Backend.NULL: miniaudio.Backend.NULL,
        }[self]


try:  # miniaudio 是 gui/edit 的可选依赖，分析 CLI 不引入。
    import miniaudio  # noqa: F401

    _HAVE_MINIAUDIO = True
except Exception:  # pragma: no cover - 依赖缺失时只降级，不崩。
    _HAVE_MINIAUDIO = False


class PlayState(str, Enum):
    STOPPED = "stopped"
    PLAYING = "playing"
    PAUSED = "paused"


class DeviceInfo:
    """播放设备（名字是稳定句柄；`ma_id` 是 cdata，不可序列化、不参与比较/哈希）。"""

    __slots__ = ("name", "backend", "sample_rates", "channels", "ma_id")

    def __init__(self, name: str, backend: Backend, sample_rates: list[int] | None = None,
                 channels: int = 2, ma_id: object = None):
        self.name = name
        self.backend = backend
        self.sample_rates = list(sample_rates or [])
        self.channels = channels
        self.ma_id = ma_id

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, DeviceInfo):
            return NotImplemented
        return (self.name == other.name and self.backend == other.backend
                and self.sample_rates == other.sample_rates and self.channels == other.channels)

    def __hash__(self) -> int:
        return hash((self.name, self.backend, tuple(self.sample_rates), self.channels))

    def __repr__(self) -> str:
        return "DeviceInfo(name=%r, backend=%s, rates=%r, ch=%d)" % (
            self.name, self.backend.value, self.sample_rates, self.channels)


def enumerate_playback_devices(backends=None) -> list[DeviceInfo]:
    """枚举播放设备。

    返回非空列表（至少含一个 `NULL` 后端虚拟设备，保证 headless 也能构造引擎）。
    某后端不可用时 miniaudio 会抛 `ma.Error` ⇒ 逐后端吞掉，返回能拿到的。
    """
    if not _HAVE_MINIAUDIO:
        return [DeviceInfo(name="(无 miniaudio)", backend=Backend.NULL)]

    import miniaudio as _ma

    want = backends or [Backend.WASAPI, Backend.DSOUND, Backend.WINMM, Backend.NULL]
    out: list[DeviceInfo] = []
    seen: set[tuple[str, Backend]] = set()
    for b in want:
        try:
            devs = _ma.Devices(backends=[b.ma]).get_playbacks()
        except Exception:
            continue
        for d in devs:
            name = d.get("name") or "(未命名设备)"
            rates = sorted({f["samplerate"] for f in d.get("formats", [])})
            ch = max((f["channels"] for f in d.get("formats", [])), default=2)
            info = DeviceInfo(name=name, backend=b, sample_rates=rates, channels=ch, ma_id=d.get("id"))
            key = (name, b)
            if key in seen:
                continue
            seen.add(key)
            out.append(info)
    if not out:  # 极端情况：全失败也至少给一个 NULL。
        out.append(DeviceInfo(name="(NULL 虚拟)", backend=Backend.NULL))
    return out


def _find_device(name: str, backend: Backend | None, pool: list[DeviceInfo]) -> DeviceInfo | None:
    """据名字（必要时+后端）从枚举池里回查，返回带 `ma_id` 的那条。"""
    for d in pool:
        if d.name == name and (backend is None or d.backend == backend):
            return d
    return None


class Transport:
    """纯传输状态机：不碰声卡、不碰 Qt，全部可 headless 测。

    职责：
    - 维护 `state` 与播放头 `position`（秒）；
    - 接受一段可选 `loop` 选区段 `[loop_t0, loop_t1)`（试听选区），play 时从 `loop_t0` 起、
      越 `loop_t1` 回到 `loop_t0`（loop 模式）或停在 `loop_t1`；
    - `seek` 夹紧到 `[0, duration]`；
    - `advance(dt)` 推进播放头（真实引擎在回调里按帧数调它；测试用模拟时间推进）；
    - 状态转移非法时不崩、返回 `False`。
    """

    def __init__(self, duration: float = 0.0, fs: int = 44100):
        self.duration = float(duration)
        self.fs = int(fs)
        self._state = PlayState.STOPPED
        self._pos = 0.0
        self._loop: tuple[float, float] | None = None
        self._lock = threading.Lock()

    # ---- 只读访问 ----
    @property
    def state(self) -> PlayState:
        return self._state

    @property
    def position(self) -> float:
        return self._pos

    @property
    def loop(self) -> tuple[float, float] | None:
        return self._loop

    @property
    def is_playing(self) -> bool:
        return self._state is PlayState.PLAYING

    # ---- 选区段 ----
    def set_loop(self, t0: float, t1: float) -> bool:
        """设置试听选区段 `[t0, t1)`。越界回弹、t0>=t1 拒绝。"""
        if self.duration <= 0:
            return False
        t0 = min(max(0.0, float(t0)), self.duration)
        t1 = min(max(0.0, float(t1)), self.duration)
        if t1 - t0 < 1e-9:
            return False
        with self._lock:
            self._loop = (t0, t1)
        return True

    def clear_loop(self) -> None:
        with self._lock:
            self._loop = None

    # ---- 转移 ----
    def play(self) -> bool:
        with self._lock:
            if self._state is PlayState.PLAYING:
                return True  # 幂等
            if self.duration <= 0:
                return False
            # 从选区段起点起播；无选区从头（或续播点）起。
            if self._loop is not None:
                self._pos = self._loop[0]
            elif self._pos >= self.duration - 1e-9:
                self._pos = 0.0
            self._state = PlayState.PLAYING
        return True

    def pause(self) -> bool:
        with self._lock:
            if self._state is not PlayState.PLAYING:
                return False
            self._state = PlayState.PAUSED
        return True

    def stop(self) -> bool:
        with self._lock:
            if self._state is PlayState.STOPPED:
                return True
            self._state = PlayState.STOPPED
            self._pos = self._loop[0] if self._loop is not None else 0.0
        return True

    def toggle(self) -> bool:
        if self._state is PlayState.PLAYING:
            return self.pause()
        return self.play()

    def seek(self, t: float) -> bool:
        if self.duration <= 0:
            return False
        with self._lock:
            self._pos = min(max(0.0, float(t)), self.duration)
        return True

    # ---- 推进（真实引擎在回调里按帧数调；测试用模拟时间） ----
    def advance(self, dt: float) -> float:
        """推进播放头 `dt` 秒。返回实际推进后所在位置。

        处理选区段边界：越 `loop_t1` 时，loop 模式回 `loop_t0`，否则停在 `loop_t1` 并转 STOPPED。
        """
        with self._lock:
            if self._state is not PlayState.PLAYING:
                return self._pos
            if dt < 0:
                return self._pos
            self._pos += dt
            if self._loop is not None:
                lo, hi = self._loop
                if self._pos >= hi - 1e-9:
                    # 试听默认不循环：停在选区尾并转 STOPPED。
                    self._pos = hi
                    self._state = PlayState.STOPPED
            elif self._pos >= self.duration:
                self._pos = self.duration
                self._state = PlayState.STOPPED
        return self._pos


class PlaybackEngine:
    """miniaudio 后端，驱动 `Transport`。

    设计：持有一段内存中的 float32 音频 `data`（声道×样本），按 `Transport` 的状态通过
    miniaudio 的 generator 拉帧。播放头位置由 `Transport.advance` 按回调帧数推进——
    最小依赖 Python（只做状态机与缓冲拷贝），重活在 C 层。

    无 miniaudio 时降级为"哑引擎"：`play/pause/stop` 走 `Transport` 的模拟时钟
    （`advance` 由 `_tick` 定时器推进），保证上层逻辑与测试不崩。
    """

    def __init__(self, data: np.ndarray, fs: int, device: DeviceInfo | None = None,
                 backend: Backend = Backend.WASAPI, buffersize_msec: int = 50):
        if data.ndim == 1:
            data = data[:, None]
        self.data = np.ascontiguousarray(data, dtype=np.float32)
        self.fs = int(fs)
        self.channels = max(1, self.data.shape[1])
        self.backend = backend
        self.buffersize_msec = int(buffersize_msec)
        self.transport = Transport(duration=len(self.data) / self.fs, fs=self.fs)
        self.device = device
        self._dev = None
        self._gen_lock = threading.Lock()
        self._frame_cursor = 0  # 当前已交给 miniaudio 的样本下标
        self._underruns = 0
        self._last_pull_ts = 0.0
        self._on_position: list[Callable[[float, PlayState], None]] = []
        self._on_error: list[Callable[[str], None]] = []
        # 哑引擎用的模拟时钟
        self._tick_timer = None
        self._tick_thread = None
        self._stop_tick = threading.Event()

    # ---- 回调注册（Qt 层接播放头） ----
    def on_position(self, cb: Callable[[float, PlayState], None]) -> None:
        self._on_position.append(cb)

    def on_error(self, cb: Callable[[str], None]) -> None:
        self._on_error.append(cb)

    # ---- 设备枚举辅助 ----
    @staticmethod
    def list_devices() -> list[DeviceInfo]:
        return enumerate_playback_devices()

    # ---- 控制 ----
    def play(self) -> bool:
        ok = self.transport.play()
        if not ok:
            return False
        # NULL 后端是 headless 路径：miniaudio 的 NULL 设备不拉帧 ⇒ 永远走哑时钟。
        # 仅在"有 miniaudio 且选了真实后端"时才起 C 层回调。
        if self.backend is Backend.NULL:
            self._start_dummy_clock()
        elif _HAVE_MINIAUDIO:
            try:
                self._start_device()
            except Exception as e:  # 设备拔了/初始化失败 ⇒ 记错误，不崩。
                self._emit_error("设备启动失败：%s" % e)
                self.transport.stop()
                return False
        else:
            self._start_dummy_clock()
        self._emit_position()
        return True

    def pause(self) -> bool:
        ok = self.transport.pause()
        self._teardown()
        self._emit_position()
        return ok

    def stop(self) -> bool:
        ok = self.transport.stop()
        self._teardown()
        self._emit_position()
        return ok

    def toggle(self) -> bool:
        # Transport.toggle 已flip状态；这里据新状态起/停真实后端。
        if not self.transport.toggle():
            return False
        if self.transport.is_playing:
            return self.play()
        return self.pause()

    def seek(self, t: float) -> bool:
        ok = self.transport.seek(t)
        with self._gen_lock:
            self._frame_cursor = int(round(t * self.fs))
        self._emit_position()
        return ok

    def set_loop(self, t0: float, t1: float) -> bool:
        return self.transport.set_loop(t0, t1)

    def clear_loop(self) -> None:
        self.transport.clear_loop()

    def set_source_data(self, data: np.ndarray, *, keep_position: bool = True) -> None:
        """A/B 切换时**原地换缓冲**：不重建 miniaudio 设备（重建会爆音/丢帧）。

        仅改内存里的 `data` 与 `Transport.duration`，播放头按"比例"夹到新时长内，
        正在播放也不中断（下一帧回调直接从新 buffer 拉）。真实设备用 `device_id`
        句柄保持稳定 ⇒ 这是安全的。
        """
        if data.ndim == 1:
            data = data[:, None]
        data = np.ascontiguousarray(data, dtype=np.float32)
        new_dur = len(data) / self.fs
        with self._gen_lock:
            ratio = (self.transport.position / self.transport.duration) if self.transport.duration > 0 else 0.0
            self.data = data
            self.channels = max(1, data.shape[1])
            was_playing = self.transport.is_playing
            self.transport = Transport(duration=new_dur, fs=self.fs)
            if keep_position and was_playing:
                self.transport._pos = min(max(0.0, ratio * new_dur), new_dur)
                self.transport._state = PlayState.PLAYING
            self._frame_cursor = int(round(self.transport.position * self.fs))
        self._emit_position()

    @property
    def underruns(self) -> int:
        return self._underruns

    # ---- miniaudio 后端 ----
    def _start_device(self) -> None:
        import miniaudio as _ma

        pool = enumerate_playback_devices([self.backend])
        dev = _find_device(self.device.name, self.backend, pool) if self.device else None
        if dev is None and pool:
            dev = pool[0]
        self._frame_cursor = int(round(self.transport.position * self.fs))
        self._dev = _ma.PlaybackDevice(
            output_format=_ma.SampleFormat.FLOAT32,
            nchannels=self.channels,
            sample_rate=self.fs,
            buffersize_msec=self.buffersize_msec,
            device_id=dev.ma_id if dev and dev.ma_id else None,
            backends=[self.backend.ma],
        )
        self._dev.start(self._frame_generator())

    def _frame_generator(self) -> Iterator[bytes]:
        """miniaudio 契约：接收 `framecount`（`.send()`），yield `bytes`。

        **单一 yield 的规范写法**（否则每两次回调会拿到空帧，真实后端会爆音）：
        首帧 `next(gen)` 停在 `framecount = yield b""`（返回 priming 空帧）；
        之后每次 `gen.send(framecount)` 恢复，算出本帧样本，用同一个 yield 把字节交还。
        """

        nch = self.channels
        framecount = yield b""  # 首帧：miniaudio next() 进来，先给一个空帧
        if framecount is None:
            framecount = 2048
        while True:
            now = time.monotonic()
            if self._last_pull_ts and (now - self._last_pull_ts) > (framecount / self.fs * 3 + 0.05):
                self._underruns += 1  # 近似 X-run：拉取间隔异常大。
            self._last_pull_ts = now
            with self._gen_lock:
                start = self._frame_cursor
                end = start + int(framecount)
                if start >= len(self.data):
                    self.transport.advance(framecount / self.fs)
                    self._emit_position()
                    self._frame_cursor = len(self.data)
                    framecount = yield np.zeros((int(framecount), nch), dtype=np.float32).tobytes()
                    continue
                seg = self.data[start:end]
                self._frame_cursor = end
            if len(seg) < int(framecount):
                seg = np.pad(seg, ((0, int(framecount) - len(seg)), (0, 0)))
            self.transport.advance(len(seg) / self.fs)
            self._emit_position()
            framecount = yield seg.tobytes()

    # ---- 哑引擎（无 miniaudio / NULL 后端）：模拟时钟推进 ----
    def _start_dummy_clock(self) -> None:
        # 哑时钟：无论有无 miniaudio，NULL 后端都靠它推进播放头（headless 可测）。
        self._stop_tick.clear()
        self._tick_thread = threading.Thread(target=self._dummy_tick, daemon=True)
        self._tick_thread.start()

    def _dummy_tick(self) -> None:
        period = 0.025  # 25 ms，落在 [20,60] 区间
        while not self._stop_tick.is_set() and self.transport.is_playing:
            self.transport.advance(period)
            self._emit_position()
            time.sleep(period)
        self._stop_tick.set()

    def _teardown(self) -> None:
        if self._dev is not None:
            try:
                self._dev.stop()
                self._dev.close()
            except Exception:
                pass
            self._dev = None
        if self._tick_thread is not None:
            self._stop_tick.set()
            self._tick_thread = None

    # ---- 事件 ----
    def _emit_position(self) -> None:
        for cb in self._on_position:
            try:
                cb(self.transport.position, self.transport.state)
            except Exception:
                pass

    def _emit_error(self, msg: str) -> None:
        for cb in self._on_error:
            try:
                cb(msg)
            except Exception:
                pass

    def __del__(self):
        try:
            self._teardown()
        except Exception:
            pass
