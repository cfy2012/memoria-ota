# memoria-ota

Memoria OS 礼物机的 OTA 部署仓库：**固件更新包 + 应用商店程序包**。源码仓库与此分离，本仓库只放部署产物。

## 结构

```
memoria-ota/
├── manifest.json          应用商店总清单（设备商店先扒这个）
├── List.md                人读版清单 + 发布手册
├── firmware/
│   ├── version.json       固件更新清单（version / url / major / changelog）
│   └── memoria_os_*.bin   固件更新包（按版本号命名）
└── store/
    └── <应用名>/           每个应用一个文件夹
        └── <应用名>_v*.msp  该应用全部历史版本包（从旧到新，永不删除）
```

## 规则

1. **包只增不删**：历史版本保留，出问题可回滚
2. **先放包再改清单**：清单指向的文件必须已存在
3. **version.json 的 version 字段与 bin 实际版本一致**，避免设备端误判
4. 清单 JSON 格式改动前，先与固件端解析器（package_manager）对齐字段

## 设备端接入

固件内 mirror_url 指向本仓库镜像地址（NVS 运行时配置，换地址无需重编译）：

- 商店清单 / 应用包：`mirror_url` + `manifest.json`
- 固件更新：`mirror_url` 同源 + `firmware/version.json`

国内网络访问 `raw.githubusercontent.com` 不稳定时，镜像走 `cdn.jsdelivr.net/gh/cfy2012/memoria-ota@main/<路径>`。
