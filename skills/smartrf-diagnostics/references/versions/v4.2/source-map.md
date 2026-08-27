# SmartRF v4.2.x 源码地图

默认工程仓库：`/home/zhuhy/workspace/nc/sagitta/srf-v4`。SmartRF 根目录为 `subsys/wireless/smartrf_v4/`；用户传入其他仓库时以该仓库为准。

## 版本与公开契约

- `inc/smartrf.h`：协议栈版本和主 API。
- `inc/srf_api_config.h`：应用可覆盖的构建配置。
- `inc/srf_api_contract.h`：公开但不可随意覆盖的协议契约。
- `inc/srf_types.h`、`inc/srf_event.h`：公共类型和事件。

## 诊断、核心与数据路径

- `src/srf_debug.c`、`src/include/debug/`：CLI section、字段格式和模块开关。
- `src/srf_core.c`、`src/srf_session.c`、`src/srf_connect.c`、`src/handler/`：状态机和连接流程。
- `src/srf_transport.c`、`src/srf_queue.c`、`src/srf_statistics.c`、`src/srf_profile.c`：队列、统计和 profile 分发。

## CQA 与 PHY

- `src/srf_cqa.c`、`src/handler/connecting/`：Device/Host CQA、allow map 传递和建连筛选。
- `doc/channel-quality-assessment.md`：策略说明。
- `src/platform/gx83xx/srf_phy_gx83xx.c`、`src/platform/gx83xx/srf_platform_gx83xx.c`：平台 PHY、时钟、AFC prepare。
- `src/include/phy/`、`doc/phy-layer.md`：PHY 内部契约和说明。

## 私有 link layer 与库发布物

- `private/link_layer/conn_impl_c/include/`
- `private/link_layer/conn_impl_c/src/`
- `libs/conn_impl_c/include/`
- `libs/conn_impl_c/debug/`
- `libs/conn_impl_c/srf_conn_impl_c.manifest`
- `doc/handler/connected/conn_impl_c.md`

Profile manifest 示例：`libs/hid_mouse/srf_hid_mouse.manifest`。

## 字段核对顺序

对一个可疑字段只做四步：

1. `rg` 查 section 标题或字段标签，找到打印点。
2. 跟到诊断结构定义，确认单位、宽度、哨兵值和 build guard。
3. `rg` 字段写入点，确认 ISR/线程上下文和更新条件。
4. 查 init/reset/stats-clear/disconnect，确认生命周期。

对封库实现，先读随库发布的 debug header、dump 源码和 manifest。release 库没有内部源码或字段时，明确能力边界，不从相邻版本私有源码反推确定结论。

