# 共帧采录

新采集头把一份原帧送给两路，甲乙报头分别占各自的传输位。报头注明卷号、链路原序、测点、出处和实采帧号。链路和原序仍用原工程的口径。帧号在卷内指认实采原件，正文是整数读数和原文。两个帧号可有相同读数和原文。

## 作业约定

- 在 `station.roster.register` 增加 `register_shared(database, roll_id, shift, positions)`，沿用普通登记的清册输入和返回值。卷号登记后固定其采录模式。共帧卷的结束声明仍用 `accept(database, roll_id, LaneEnd(...))`。结束声明可以先到，0 表示空路。序号范围校验适用于报头。共帧卷拒收普通 `Delivery`，普通卷拒收共帧件。
- 在 `station.capture.intake` 增加 `accept_header(database, header, device)` 和 `accept_frame(database, frame, device)`。两者分别核对所交材料与设备上同一身份的原件。首次实收才需要从设备核对原件，重投与已实收内容比较。同号改报头的帧号、测点或出处，或同帧改原文、读数，抛出 `ValueError`。报头实收返回 `header` 和 `confirmation` 两个属性，确认值沿用 `pending`、`unknown`、`released`。正文实收返回该 `Frame`。已收报头的续办可单独进行，正文仍欠收时按设备现有保管情况办理。
- 在 `station.capture.recovery` 增加 `find_header(database, identity)`、`find_frame(database, roll_id, frame_id)` 和 `recover_shared(database, roll_id, device)`。两个查询从站库返回对应实收结果，未收返回 `None`。续办针对指定卷，核对原归还结局、归还符合资格的已收报头，并在正文持久实收后清理该帧的额外设备保管。站库重开用原设备会话续办，设备完全停源时仍可查询实收和签件。
- 共帧卷继续用 `seal` 和 `get`。签发仍返回 `waiting`、`conflict` 或 `issued`。待收结果的 `waiting_lanes` 列出结束声明或范围内报头尚欠的链路，`waiting_frames` 列出实收报头所指的欠正文帧号。按普通卷的等值镜像、异值冲突和缺测口径核定。签件沿用 `roll_id`、`shift`、`positions`、`measured`、`missing` 和 `receipt` 属性。逐原位的 `sources` 保持原链路、原序、读数和出处，另有 `frame_id`。卷级 `frames` 按帧号索引实际引用的 `Frame`，同一帧保存一份。正文和首执一次签存，失败后可用原实收续签。普通卷的返回和持久旧签件继续照原格式读取。

## 随附设备

`examples/shared_frame_device.py` 给出 `Header`、`Frame` 和可控设备 `FrameDevice`。装入一次新原帧时，`stage(frame, *headers)` 把一张或两张报头放到对应的空位。两路可分别读取和归还，报头实际交站的先后由调用方决定。`read_header(lane)` 读当前占位报头，空位返回 `None`。`read_frame(roll_id, frame_id)` 读当前仍可取的原正文，正文口断开时报 `SourceDisconnected`，正文已回收时报 `KeyError`。`set_body_online(False)` 只关闭正文口，`set_online(False)` 关闭全部站端访问。

`return_slot(header)` 执行物理归还，原位须仍是同一报头。`lose_next_return_reply()` 让下一次真正完成的归还抛出 `ReleaseReplyLost`。`returned_header(identity)` 可在设备在线时核查实际归还过的原报头。`references(roll_id, frame_id)` 返回当前占位链路及可选的 `hold`。`hold_frame` 和 `drop_hold` 以卷号和帧号操作一条独立硬件保管引用。没有任何引用时设备回收正文。各动作共用 `device.serialized` 提供的可重入临界区。夹具只执行硬件动作，设备访问和站库写入可能各自失败。`tests.test_frame_device.reject_next_write_commit()` 可用于已初始化的 SQLite 站库，拒绝随后首个实际写入行的提交，以检查提交失败边界。

## 夜班回放与运行

`examples/shared-frame.json` 中 R28 的 `shared` 是共同原帧 F 及两路报头。每组用外层 `roll_id` 构造正文及报头，组内报头共用该组正文的 `frame_id`。以甲、乙各截止 1 为一卷，先断正文，再实收其中一路报头。另一路仍留在源也应可归还先到位。允许末报头继续保管 F，也允许单独保管 F 后归还末位。两路报头与截点齐备时，仍须等 F。补收 F 并断源重开，原位 1 应显示 7 和 A1、B1，两出处同指原文为 f7 的 F。原位 2 为缺测，按原卷号再领仍是同一首执。

若要验证先归还的一路能继续采，在它腾位后使用 `next_a` 或 `next_b`，相应路截止改为 2。这个分支中 G 与 F 同文同值。恢复正文口后先收 G，仍欠 F。归还失答后，以原身份查结，晚到的旧报头重投不得影响复用其位的新报头。用一次报头提交失败核对槽位保持，用一次正文提交失败核对 F 的最后保管引用仍在。只有两类证据都齐备才有签件。

沿用根目录的 `.pointwise-runtime.json`。保留版本 1、`kind` 为 `cli`、`setup` 为命令 argv 列表的运行方式。`verify` 和 `demo` 各用一个 argv 数组。核验入口须运行原 `check_project.py` 并检查共帧作业，失败退出非零。演示入口打印实际回放的腾位和停源领卷结果。
