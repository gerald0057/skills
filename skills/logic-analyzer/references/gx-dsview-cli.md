# gx-dsview-cli

当前环境优先使用 `/home/zhuhy/opt/gx-dsview/bin/gx-dsview-cli`，其次使用 PATH 中的命令，最后检查 `~/workspace/projects/ideas/gx-dsview/build/gx-dsview-cli`。可用 `--gx-cli` 显式指定其他绝对路径。

工具只通过参数数组执行 CLI，并限制运行时间及 stdout/stderr 大小。`.dsl` 分析必须调用 `inspect --json` 和 `validate --json`。采集依次调用 `devices --json`、`capabilities --device N --json` 和：

```text
capture --device N --samplerate RATE --channels LIST --samples COUNT
        --trigger TRIGGER --pretrigger PERCENT --timeout SECONDS
        --output PATH --no-private-decode --json
```

设备枚举的 `status=unknown` 不代表设备可用；以 capabilities/capture 实际 open 结果为准。同一 USB 设备只允许一个 CLI 或 GUI 进程控制。

`waveform_tool.py acquire` 只执行设备检查和一次采集，生成 `.dsl` 后立即返回；适合需要明确采集边界或把分析安排在后续步骤的场景。`waveform_tool.py capture` 复用相同采集过程，并继续执行预检、inspect、validate 和波形测量。两者都是通用逻辑分析功能，不包含 DUT 或用户操作语义。

完整命令面以当前 gx-dsview 源码树的 `docs/cli.md` 为权威来源。不要假定其他 DSView、sigrok 或 GUI 参数可以混用。
