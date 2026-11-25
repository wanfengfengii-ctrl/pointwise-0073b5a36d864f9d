# 冷轧夜班巡检站

班长登记带材卷、班次和有序测点。采集员接管同一仪器经甲、乙两条链路发来的读数。质检员查看当前实收和冲突，值班员签发，次班按卷号领取固定班卷并复查其中的缺测点。

## 运行

使用 Python 3.11 标准库。在项目根目录执行完整回归。

```sh
python3 -B check_project.py
```

复演一班作业并输出预览、签发尝试、固定班卷和次班复查点。演示使用独立的临时站库。

```sh
python3 -B -m station.source.replay demo examples/night-shift.json
```

使用自选站库，随后按原卷号领取。

```sh
python3 -B -m station.source.replay run night.sqlite examples/night-shift.json
python3 -B -m station.source.replay get night.sqlite R17
```

`run` 和 `demo` 在班卷成功签发时退出 0，卷仍待收或因冲突未签时退出 1。输入、设备或存储错误退出 2。`get` 对未签卷输出 `null` 并退出 1。重跑录制会复用既有登记和实收，已签班卷的签发检查返回原成果。

## 录制与作业顺序

录制为含 `roll_id`、`shift`、`positions`、`events` 的 JSON 对象。卷号、班次、原位和出处为非空白字符串。来件事件 `delivery` 含 `lane`、`sequence`、`position`、`value`、`origin` 和原文字符串 `raw`。链路用 `A`、`B` 表示甲、乙，序号为正整数，读数为整数。`end` 含 `lane` 和非负整数 `last_sequence`。`lose_reply`、`disconnect` 和 `reconnect` 含 `lane`，分别设置下一次实际释放失答、切源和恢复原会话。`preview` 和 `seal` 在事件序列中留下作业检查点，事件结束后再尝试签发并按号领取。重连沿用设备会话中的释放结局和未交事件。另开录制会话用于独立复演。

1. 班长登记 R17 和按原序排列的测点 1、2、3。
2. 甲路送来序号 1、原位 1 的 7 和出处 A1，然后声明结束 1。站端先保存原件，再释放设备来件。
3. 质检员看到原位 1 已见，2、3 待收。本次签发待收，次班暂时无卷可领。
4. 乙路先声明结束 2，接着送来序号 2、原位 2 的 11 和 B2。设备完成释放但丢失回复。切源后，11 和 B2 可凭站库找回，乙/1 仍待收。
5. 乙路恢复原会话，补交尚未释放的序号 1、原位 1 的 7 和 B1。预览此时确认 3 号缺测，原位 1 保留 A1、B1。
6. 两路切源后签发。正文为 7、11、缺测，实测二笔，缺测一笔，同时生成首执。
7. 次班按 R17 领取原顺序、原出处和首执，据此只复查 3 号。

## Python API

| 调用 | 结果与边界 |
| --- | --- |
| `station.source.replay.initialize_station(database)` | 补建 SQLite 表，保留已有清册、原件及签发结果 |
| `station.roster.register.register(database, roll_id, shift, positions)` | 返回固定清册。同参复用，同号异参及非法清册抛出 `ValueError`。`positions` 可传字符串迭代对象或 `Position` 对象迭代对象 |
| `station.source.session.open_session(recording, lane)` 和 `offer(session)` | 每次推进本路一个事件，返回 `Delivery`、`LaneEnd` 或 `None`。控制事件或事件已耗尽返回 `None`。断源报 `SourceDisconnected`。已释放来件报 `DeliveryReleased`，原载荷无法从该会话重读 |
| `station.capture.intake.accept(database, roll_id, event, session=None)` | 返回实收 `CaptureEvidence` 或持久的 `LaneEnd`。传入会话时，首次接管核对设备交件，提交后续办释放。省略会话时只接管给定的原件或结束声明。错卷、错位、不同载荷的同号来件及矛盾截点抛出 `ValueError`，未知卷抛出 `KeyError` |
| `station.capture.recovery.find_accept_result(database, identity)` 和 `recover_capture(database, roll_id, session=None)` | 按原身份查实收，或按卷恢复完整证据快照。设备可用时可按原会话续办待核确认 |
| `station.preview.progress.progress(database, roll_id)` | 返回当前证据的原位视图、冲突和计数。未知卷抛出 `KeyError` |
| `station.archive.issue.seal(database, roll_id)` | 返回 `waiting`、`conflict` 或 `issued` 的 `IssueResult`。已签卷复用首执。未知卷抛出 `KeyError`，写入失败向调用方报告 SQLite 错误 |
| `station.archive.retrieve.get(database, roll_id)` | 领取指定卷的正文和首执，未签返回 `None` |

来件身份是 `(roll_id, lane, sequence)`。同参重投须保留原位、读数、出处和原文。声明确认本路序号的上界，声明可以越过范围内的来件，0 表示确定空路。归档在同一事务读取清册和证据，先核实两路各有结束声明及从 1 到截点的全部持久实收，再逐原位核值。缺少任一路声明或范围内来件时只报待收。实收和确认结局分别记录，释放失答后的实收仍计入覆盖。

等值读数按原位计一笔，保留原位内的所有来件出处。异值镜像在预览中展示原读数，覆盖齐备后阻止整卷签发。覆盖齐备且两路均无来件的原位列为缺测，正文读数为 `null`。数值 0 是实测。读数在仪器原单位下用精确整数保存。正文沿用清册顺序，出处按链路及数字序号排序。

清册同号同参可重复登记，首个合法接管提交时记录开始采录。接管写失败时设备保留原件。设备释放失答时，站端保存待核状态和完整原件。班卷正文和首执同一事务提交，签存失败可以停源后用原证据续签。旧卷始终按请求卷号领取。
