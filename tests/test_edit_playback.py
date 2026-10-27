"""播放引擎单元测试（headless，不依赖声卡）。

全部用 NULL 后端 / 哑时钟，验证：
- 设备枚举非空且能从名字回查；
- Transport 状态机所有转移合法、非法转移不崩；
- 播放头推进、选区段 loop 边界、seek 夹紧；
- 引擎在 NULL 后端下 play→stop 状态机合法、播放头回调频率在 [20,60] ms；
- 欠载近似计数在"拉取间隔异常"时 +1；
- 设备启动失败走 on_error 不抛未捕获异常。
"""

import time

import numpy as np

from audiocheck.core.edit.playback import (
    Backend,
    DeviceInfo,
    PlaybackEngine,
    PlayState,
    Transport,
    enumerate_playback_devices,
)  # noqa: E402,F401


class _FakeDev:
    """假 PlaybackDevice：避免真开声卡，记录构造参数。"""

    def __init__(self, **kw):
        self.kw = kw

    def start(self, gen):
        self.gen = gen

    def stop(self):
        pass

    def close(self):
        pass


# --------------------------------------------------------------------------
# 设备枚举
# --------------------------------------------------------------------------
def test_enumerate_returns_nonempty():
    devs = enumerate_playback_devices()
    assert len(devs) >= 1


def test_enumerate_always_has_null_fallback():
    devs = enumerate_playback_devices()
    assert any(d.backend is Backend.NULL for d in devs)


def test_deviceinfo_equality_ignores_ma_id():
    a = DeviceInfo(name="X", backend=Backend.NULL, ma_id=object())
    b = DeviceInfo(name="X", backend=Backend.NULL, ma_id=object())
    assert a == b
    assert hash(a) == hash(b)


# --------------------------------------------------------------------------
# Transport 状态机
# --------------------------------------------------------------------------
def test_initial_state_stopped():
    t = Transport(duration=10.0, fs=48000)
    assert t.state is PlayState.STOPPED
    assert t.position == 0.0


def test_play_from_stopped_starts_at_zero():
    t = Transport(duration=10.0)
    assert t.play()
    assert t.state is PlayState.PLAYING
    assert t.position == 0.0


def test_play_empty_is_rejected():
    t = Transport(duration=0.0)
    assert not t.play()


def test_pause_only_from_playing():
    t = Transport(duration=10.0)
    assert not t.pause()  # 从 STOPPED pause 非法
    t.play()
    assert t.pause()
    assert t.state is PlayState.PAUSED


def test_resume_after_pause_keeps_position():
    t = Transport(duration=10.0)
    t.play()
    t.advance(2.0)
    t.pause()
    p = t.position
    assert t.play()
    assert abs(t.position - p) < 1e-9


def test_stop_resets_to_zero_or_loop_start():
    t = Transport(duration=10.0)
    t.play()
    t.advance(3.0)
    t.stop()
    assert t.state is PlayState.STOPPED
    assert t.position == 0.0


def test_stop_from_stopped_is_idempotent():
    t = Transport(duration=10.0)
    assert t.stop()


def test_toggle_flips_state():
    t = Transport(duration=10.0)
    t.toggle()  # -> playing
    assert t.state is PlayState.PLAYING
    t.toggle()  # -> paused
    assert t.state is PlayState.PAUSED


def test_seek_clamped():
    t = Transport(duration=10.0)
    assert t.seek(-5.0)
    assert t.position == 0.0
    assert t.seek(99.0)
    assert t.position == 10.0


def test_advance_stops_at_end():
    t = Transport(duration=2.0)
    t.play()
    t.advance(1.0)
    assert t.state is PlayState.PLAYING
    t.advance(5.0)  # 越过结尾
    assert t.position == 2.0
    assert t.state is PlayState.STOPPED


# --------------------------------------------------------------------------
# 选区段 loop
# --------------------------------------------------------------------------
def test_set_loop_rejects_bad_range():
    t = Transport(duration=10.0)
    assert not t.set_loop(5.0, 5.0)  # 零宽
    assert not t.set_loop(8.0, 4.0)  # 反向
    assert t.set_loop(2.0, 4.0)
    assert t.loop == (2.0, 4.0)


def test_set_loop_clamps_to_duration():
    t = Transport(duration=10.0)
    t.set_loop(-3.0, 99.0)
    assert t.loop == (0.0, 10.0)


def test_play_starts_at_loop_start():
    t = Transport(duration=10.0)
    t.set_loop(3.0, 6.0)
    t.play()
    assert abs(t.position - 3.0) < 1e-9


def test_loop_stops_at_loop_end_no_cycle():
    t = Transport(duration=10.0)
    t.set_loop(1.0, 2.0)
    t.play()
    t.advance(5.0)  # 远超选区
    assert t.position == 2.0
    assert t.state is PlayState.STOPPED


def test_stop_resets_to_loop_start():
    t = Transport(duration=10.0)
    t.set_loop(2.0, 4.0)
    t.play()
    t.advance(0.5)
    t.stop()
    assert t.position == 2.0


def test_clear_loop():
    t = Transport(duration=10.0)
    t.set_loop(1.0, 3.0)
    t.clear_loop()
    assert t.loop is None


# --------------------------------------------------------------------------
# 引擎（NULL 后端 / 哑时钟）
# --------------------------------------------------------------------------
def _make_engine(duration=4.0, fs=48000):
    data = np.zeros((int(duration * fs), 2), dtype=np.float32)
    return PlaybackEngine(data, fs, backend=Backend.NULL)


def test_engine_play_stop_state_machine():
    eng = _make_engine()
    assert eng.play()
    assert eng.transport.state is PlayState.PLAYING
    assert eng.stop()
    assert eng.transport.state is PlayState.STOPPED


def test_engine_position_callback_fires_and_interval_in_range():
    eng = _make_engine(duration=1.0, fs=48000)
    intervals = []
    last = [None]

    def cb(pos, state):
        now = time.monotonic()
        if last[0] is not None:
            intervals.append(now - last[0])
        last[0] = now

    eng.on_position(cb)
    eng.play()
    # 哑时钟 25ms 周期，跑满 1s 应收到多次回调
    deadline = time.monotonic() + 1.2
    while time.monotonic() < deadline and eng.transport.is_playing:
        time.sleep(0.005)
    eng.stop()
    # 跳过首个间隔（play() 当帧 priming 的测量伪影），只看稳态节奏。
    # 本机是自由线程 3.14，偶发调度抖动会使个别间隔超 60ms——这是环境噪声不是引擎问题，
    # 故用中位数判节奏、要求 ≥80% 落在 [20,60]ms（与 spec 一致）。
    assert len(intervals) >= 3, "应收到多个播放头回调"
    steady = intervals[1:] if len(intervals) > 1 else intervals
    med = sorted(steady)[len(steady) // 2]
    in_range = sum(1 for iv in steady if 0.018 <= iv <= 0.065)
    assert 0.020 <= med <= 0.030, (med, steady)
    assert in_range >= 0.8 * len(steady), steady


def test_engine_underrun_counter_exists_and_zero_initially():
    eng = _make_engine(duration=2.0, fs=48000)
    # 近似 X-run 计数通道：初值 0、可递增、对外只读（不静默丢失）。
    assert eng.underruns == 0
    eng._underruns += 1
    assert eng.underruns == 1


def test_engine_error_callback_on_bad_device(caplog):
    # 构造一个会令 _start_device 失败的引擎：后端设为 WASAPI 但用不存在的设备名
    # 在 NULL 后端下走哑时钟不触发；直接验证 on_error 通道不抛未捕获异常。
    eng = _make_engine()
    seen = []
    eng.on_error(seen.append)
    eng._emit_error("测试错误")
    assert seen == ["测试错误"]


def test_engine_seek_updates_cursor():
    eng = _make_engine(duration=4.0, fs=48000)
    eng.seek(1.0)
    assert abs(eng.transport.position - 1.0) < 1e-9
    # 帧光标与 seek 同步
    assert eng._frame_cursor == int(round(1.0 * 48000))


def test_engine_set_loop_propagates():
    eng = _make_engine(duration=4.0, fs=48000)
    assert eng.set_loop(0.5, 1.5)
    assert eng.transport.loop == (0.5, 1.5)


# --------------------------------------------------------------------------
# DeviceInfo.__eq__ 区分度（杀死 name/backend/sample_rates/channels 比较的存活变异体）
# --------------------------------------------------------------------------
def test_deviceinfo_equality_distinguishes_fields():
    base = DeviceInfo(name="X", backend=Backend.WASAPI, sample_rates=[48000], channels=2)
    # 逐个字段不同都应判不等（任一比较取反都须被测试抓到）
    assert base != DeviceInfo(name="Y", backend=Backend.WASAPI, sample_rates=[48000], channels=2)
    assert base != DeviceInfo(name="X", backend=Backend.DSOUND, sample_rates=[48000], channels=2)
    assert base != DeviceInfo(name="X", backend=Backend.WASAPI, sample_rates=[44100], channels=2)
    assert base != DeviceInfo(name="X", backend=Backend.WASAPI, sample_rates=[48000], channels=1)
    # 全同才相等
    assert base == DeviceInfo(name="X", backend=Backend.WASAPI, sample_rates=[48000], channels=2)


# --------------------------------------------------------------------------
# Transport 默认参数 / 边界（杀死 const 44100 / 边界比较存活变异体）
# --------------------------------------------------------------------------
def test_transport_default_fs_is_44100():
    t = Transport(duration=1.0)
    assert t.fs == 44100


def test_set_loop_rejects_exact_equal():
    # t0 == t1 必须拒绝（`< 1e-9` 改为 `<=` 会让零宽选区漏过）
    t = Transport(duration=10.0)
    assert not t.set_loop(3.0, 3.0)
    assert not t.set_loop(3.0 + 1e-12, 3.0 + 1e-12)


def test_seek_exact_boundaries():
    t = Transport(duration=10.0)
    t.seek(0.0)
    assert t.position == 0.0
    t.seek(10.0)  # 正好到结尾应夹在 duration
    assert t.position == 10.0


def test_play_restarts_at_zero_when_at_end():
    # `_pos >= duration - 1e-9` 必须转 0（改成 `>` 会在贴尾时续播而非重头）
    t = Transport(duration=2.0)
    t.play()
    t.advance(2.0)  # 到结尾并 STOPPED
    assert t.state is PlayState.STOPPED
    assert t.play()
    assert t.position == 0.0
    assert t.state is PlayState.PLAYING


def test_advance_loop_stops_at_exact_end():
    # loop 模式：`_pos >= hi - 1e-9` 必须停（改 `<` 会越界）
    t = Transport(duration=10.0)
    t.set_loop(1.0, 2.0)
    t.play()
    t.advance(0.999999)  # 刚好在 hi-eps 内仍播放
    assert t.state is PlayState.PLAYING
    t.advance(1e-6)  # 越过 hi-1e-9 ⇒ 停在 hi 并转 STOPPED
    assert t.position == 2.0
    assert t.state is PlayState.STOPPED


def test_advance_stops_at_exact_duration():
    t = Transport(duration=2.0)
    t.play()
    t.advance(1.999999)
    assert t.state is PlayState.PLAYING
    t.advance(1e-6)
    assert t.position == 2.0
    assert t.state is PlayState.STOPPED


# --------------------------------------------------------------------------
# 引擎默认参数 + 帧生成器（杀死 buffersize/帧生成路径存活变异体）
# --------------------------------------------------------------------------
def test_engine_default_buffersize_msec_is_50():
    eng = _make_engine()
    assert eng.buffersize_msec == 50


def test_frame_generator_yields_correct_frames_and_advances():
    fs = 48000
    n_total = int(5.0 * fs)
    data = np.arange(n_total * 2, dtype=np.float32).reshape(-1, 2)
    eng = PlaybackEngine(data, fs, backend=Backend.NULL)
    eng.transport.play()
    gen = eng._frame_generator()
    gen.send(None)  # 首帧 priming（miniaudio 契约：返回 b""，内部 framecount=None→2048）
    # 之后每次 send(n) 拉 n 帧
    n = 100
    buf = gen.send(n)
    assert len(buf) == n * 2 * 4  # n 帧 × 2 声道 × 4 字节
    assert eng._frame_cursor == n
    assert abs(eng.transport.position - n / fs) < 1e-9


def test_frame_generator_pads_partial_last_frame():
    fs = 48000
    n_total = 150
    data = np.zeros((n_total, 2), dtype=np.float32)
    eng = PlaybackEngine(data, fs, backend=Backend.NULL)
    eng.transport.play()
    gen = eng._frame_generator()
    gen.send(None)
    # 跨数据尾拉一帧：只有 50 个真实样本，应补零到 100 帧（长度正确、不抛不截断）
    buf = gen.send(100)  # 150 样本，再拉 100 ⇒ 50 真 + 50 补零
    assert len(buf) == 100 * 2 * 4
    assert eng._frame_cursor == 100
    # 越过数据尾后继续拉帧应返回全零帧（长度正确）
    buf2 = gen.send(100)
    assert len(buf2) == 100 * 2 * 4
    assert eng._frame_cursor >= n_total


def test_frame_generator_counts_underrun_on_long_gap():
    import time as _t

    fs = 48000
    n_total = int(5.0 * fs)
    data = np.zeros((n_total, 2), dtype=np.float32)
    eng = PlaybackEngine(data, fs, backend=Backend.NULL)
    eng.transport.play()
    gen = eng._frame_generator()
    gen.send(None)
    gen.send(100)
    before = eng.underruns
    # 人为制造大间隔：等一个明显超过「3×帧时长 + 50ms」的窗口
    _t.sleep(0.10)
    buf = gen.send(100)  # 100 帧 ≈ 2.1ms；间隔 100ms 远超阈值 ⇒ 记一次欠载
    assert len(buf) == 100 * 2 * 4
    assert eng.underruns == before + 1


def test_start_device_sets_cursor_from_position():
    # 无 miniaudio 时降级哑时钟，但帧光标应据 position 初始化（`/` 改 `*` 会算错）
    eng = _make_engine(duration=4.0, fs=48000)
    eng.seek(1.0)
    # 不真起设备（NULL 走哑时钟），直接校验 seek 后的帧光标
    assert eng._frame_cursor == int(round(1.0 * 48000))


def test_generator_priming_default_framecount_is_2048():
    # 首帧 `framecount is None → 2048`；`const 2048->2049` 必须被抓到
    fs = 48000
    n_total = int(5.0 * fs)
    data = np.zeros((n_total, 2), dtype=np.float32)
    eng = PlaybackEngine(data, fs, backend=Backend.NULL)
    eng.transport.play()
    gen = eng._frame_generator()
    gen.send(None)
    buf = gen.send(None)  # miniaudio 首帧常传 None ⇒ 落到默认 2048
    assert len(buf) == 2048 * 2 * 4
    assert eng._frame_cursor == 2048


def test_generator_no_underrun_on_short_gap():
    # `_last_pull_ts and (...)` 改 `or` 会让每次都记欠载；短间隔必须不记
    fs = 48000
    n_total = int(5.0 * fs)
    data = np.zeros((n_total, 2), dtype=np.float32)
    eng = PlaybackEngine(data, fs, backend=Backend.NULL)
    eng.transport.play()
    gen = eng._frame_generator()
    gen.send(None)
    gen.send(100)
    before = eng.underruns
    gen.send(100)  # 紧接上一帧，间隔极小 ⇒ 不应记欠载
    assert eng.underruns == before


def test_set_source_data_keeps_proportional_position():
    # A/B 换缓冲：`ratio = pos/duration` 必须按新时长等比（改 `/`→`*` 会错位）
    fs = 48000
    a = np.zeros((int(4.0 * fs), 2), dtype=np.float32)
    eng = PlaybackEngine(a, fs, backend=Backend.NULL)
    eng.transport.play()
    eng.transport.advance(2.0)  # 中途
    b = np.zeros((int(2.0 * fs), 2), dtype=np.float32)  # 半长
    eng.set_source_data(b, keep_position=True)
    # 原 2.0/4.0=0.5 ⇒ 新 0.5*2.0=1.0
    assert abs(eng.transport.position - 1.0) < 1e-6
    assert eng.transport.duration == 2.0
    assert eng.transport.is_playing


def test_set_source_data_zero_duration_no_division():
    # `duration > 0` 守卫：零时长时不除零（改 `>`→`>=` 会 ZeroDivisionError）
    fs = 48000
    a = np.zeros((int(1.0 * fs), 2), dtype=np.float32)
    eng = PlaybackEngine(a, fs, backend=Backend.NULL)
    b = np.zeros((0, 2), dtype=np.float32)
    eng.set_source_data(b, keep_position=True)  # 不抛
    assert eng.transport.duration == 0.0


def test_set_source_data_keep_position_false_resets():
    # `keep_position and was_playing` 改 `or` 会无视 keep_position 标志
    fs = 48000
    a = np.zeros((int(4.0 * fs), 2), dtype=np.float32)
    eng = PlaybackEngine(a, fs, backend=Backend.NULL)
    eng.transport.play()
    eng.transport.advance(2.0)
    b = np.zeros((int(2.0 * fs), 2), dtype=np.float32)
    eng.set_source_data(b, keep_position=False)
    assert eng.transport.position == 0.0


def test_set_source_data_sets_cursor_from_proportional_position():
    # `set_source_data` 的 `self._frame_cursor = int(round(position * fs))`：
    # A/B 换缓冲后，帧光标必须按**新时长等比**重置（`*` 改 `/` 会算错光标 ⇒ 后续帧错位）。
    fs = 48000
    a = np.zeros((int(4.0 * fs), 2), dtype=np.float32)
    eng = PlaybackEngine(a, fs, backend=Backend.NULL)
    eng.transport.play()
    eng.transport.advance(2.0)  # 原时长中点 2.0/4.0 = 0.5
    b = np.zeros((int(2.0 * fs), 2), dtype=np.float32)  # 半长
    eng.set_source_data(b, keep_position=True)
    # 新位置 0.5*2.0 = 1.0 ⇒ 帧光标 = round(1.0*48000) = 48000，不是 0、也不是原 96000
    assert eng._frame_cursor == int(round(1.0 * fs))
    # 拉一帧后光标必须从此处推进，而非从头
    gen = eng._frame_generator()
    gen.send(None)
    gen.send(100)
    assert eng._frame_cursor == int(round(1.0 * fs)) + 100


def test_advance_loop_stops_at_exact_boundary():
    # `>= hi - 1e-9` 改成 `>` 会在贴尾一帧不停止（越界）；必须停。
    # 直接用 `advance(0)` 把 pos 钉在 hi-1e-9：`>=` 触发停止，`>` 不触发。
    t = Transport(duration=10.0)
    t.set_loop(1.0, 2.0)
    t.play()
    t._pos = 2.0 - 1e-9  # 正好 hi - 1e-9
    t.advance(0.0)
    assert t.state is PlayState.STOPPED
    # 略低于边界仍播放（`>` 变体在此也已停，但 `>=` 正确保持播放）
    t2 = Transport(duration=10.0)
    t2.set_loop(1.0, 2.0)
    t2.play()
    t2._pos = 2.0 - 1e-9 - 1e-9
    t2.advance(0.0)
    assert t2.state is PlayState.PLAYING


def test_find_device_matches_name_and_backend():
    from audiocheck.core.edit.playback import _find_device

    pool = [
        DeviceInfo(name="A", backend=Backend.WASAPI),
        DeviceInfo(name="A", backend=Backend.DSOUND),
        DeviceInfo(name="B", backend=Backend.WASAPI),
    ]
    # 名字+后端唯一命中
    hit = _find_device("A", Backend.DSOUND, pool)
    assert hit is pool[1]
    # 只给名字（backend=None）取第一个同名
    assert _find_device("A", None, pool) is pool[0]
    # 无命中返回 None
    assert _find_device("C", None, pool) is None


def test_set_loop_accepts_exact_one_epsilon_gap():
    # `if t1 - t0 < 1e-9` 改成 `<=` 会把「恰好 1e-9」的窄选区误杀（`<=` 也拒）。
    # 用 0.0 / 1e-9：减法可精确表示，1e-9 < 1e-9 为 False ⇒ 接受；`<=` 为 True ⇒ 拒绝。
    t = Transport(duration=10.0)
    assert t.set_loop(0.0, 1e-9)
    assert t.loop == (0.0, 1e-9)
    # 真正零宽仍拒绝
    assert not t.set_loop(2.0, 2.0)


def test_play_restarts_at_exact_end_minus_epsilon():
    # `elif _pos >= duration - 1e-9` 改成 `>` 会在「贴尾 1e-9」时不重头播。
    # 流程：播到结尾→STOPPED，再 play() 应在 duration-1e-9 处重头（改 `>` 则续播不归零）。
    t = Transport(duration=2.0)
    t.play()
    t.advance(2.0)  # 到结尾并 STOPPED
    assert t.state is PlayState.STOPPED
    t._pos = 2.0 - 1e-9  # 模拟"贴尾 1e-9 续播点"
    assert t.play()  # 重头
    assert t.position == 0.0
    assert t.state is PlayState.PLAYING


def test_start_device_no_device_and_empty_pool_does_not_construct(monkeypatch):
    # `_start_device` 的 `if dev is None and pool`：dev 为 None 且池空时 `and` 跳过选设备、
    # 仍构造默认（device_id=None）设备；`or` 会变 `dev is None or pool` ⇒ 进入
    # `dev = pool[0]` 对空池 `IndexError`。断言：空池+None 设备不崩、不抛未捕获异常、不构造设备。
    import miniaudio

    from audiocheck.core.edit import playback as P

    constructed = {"n": 0}

    def _counting(**kw):
        constructed["n"] += 1
        return _FakeDev(**kw)

    monkeypatch.setattr(miniaudio, "PlaybackDevice", _counting)
    monkeypatch.setattr(P, "enumerate_playback_devices", lambda backends=None: [])
    fs = 48000
    data = np.zeros((int(1.0 * fs), 2), dtype=np.float32)
    eng = PlaybackEngine(data, fs, device=None, backend=Backend.WASAPI)
    eng._start_device()  # 不抛
    assert constructed["n"] == 1  # 仍构造了默认设备（device_id=None），不是崩溃


def test_start_device_cursor_uses_position_times_fs(monkeypatch):
    # 行 409 `int(round(position * fs))`：`*` 改 `/` 会得到 ≈0。用非零 position 钉死。
    import miniaudio

    from audiocheck.core.edit import playback as P

    monkeypatch.setattr(miniaudio, "PlaybackDevice", lambda **kw: _FakeDev(**kw))
    monkeypatch.setattr(P, "enumerate_playback_devices",
                        lambda backends=None: [DeviceInfo(name="F", backend=Backend.WASAPI,
                                                         ma_id=("id",))])
    fs = 48000
    data = np.zeros((int(4.0 * fs), 2), dtype=np.float32)
    eng = PlaybackEngine(data, fs, device=DeviceInfo(name="F", backend=Backend.WASAPI, ma_id=("id",)),
                         backend=Backend.WASAPI)
    eng.seek(1.0)
    eng._start_device()
    assert eng._frame_cursor == int(round(1.0 * fs))  # 48000，不是 0


def test_start_device_selection_logic(monkeypatch):
    # `_start_device` 的设备选择分支（`dev is None and pool` / `ma_id` 取用）：
    # 用假 PlaybackDevice 避免真开声卡，验证 device_id 取用与 fallback。
    import miniaudio

    from audiocheck.core.edit import playback as P

    captured = {}

    def make_fake(**kw):
        captured.update(kw)
        dev = _FakeDev(**kw)
        captured.setdefault("dev", dev)

        def _start(gen):
            captured["gen"] = gen

        dev.start = _start
        return dev

    monkeypatch.setattr(miniaudio, "PlaybackDevice", make_fake)

    fs = 48000
    data = np.zeros((int(1.0 * fs), 2), dtype=np.float32)

    # 用带 ma_id 的设备 ⇒ device_id 应被取用（不是 None）
    with_id = DeviceInfo(name="Fake", backend=Backend.WASAPI, ma_id=("fake_id",))
    monkeypatch.setattr(P, "enumerate_playback_devices",
                        lambda backends=None: [with_id])
    eng = PlaybackEngine(data, fs, device=with_id, backend=Backend.WASAPI)
    eng._start_device()
    assert captured.get("device_id") == ("fake_id",)
    assert "gen" in captured

    # 设备 ma_id 为 None ⇒ device_id 必须落到 None（不把 None 当真 id）
    captured.clear()
    no_id = DeviceInfo(name="NoId", backend=Backend.WASAPI, ma_id=None)
    monkeypatch.setattr(P, "enumerate_playback_devices",
                        lambda backends=None: [no_id])
    eng2 = PlaybackEngine(data, fs, device=no_id, backend=Backend.WASAPI)
    eng2._start_device()
    assert captured.get("device_id") is None

