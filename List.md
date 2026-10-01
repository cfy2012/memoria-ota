# Memoria OS 部署仓库

Memoria OS 礼物机的 OTA 部署源：固件更新包 + 应用商店程序包。

## 结构

```
memoria-ota/
├── manifest.json          应用商店总清单（设备先扒这个，JSON 格式）
├── List.md                本文件（人读版清单 + 发布手册）
├── firmware/
│   ├── version.json       固件更新清单（version/url/major/changelog）
│   └── memoria_os_*.bin   固件更新包（按版本号命名）
└── store/
    └── <应用名>/           每个应用一个文件夹
        └── <应用名>_v*.msp  该应用全部历史版本包（从旧到新）
```

## 发布手册

### 固件更新

1. 本地 `idf.py build` 得到 `build/memoria_os.bin`
2. 复制到 `firmware/`，改名 `memoria_os_<新版本>.bin`（如 `memoria_os_1.0.1.bin`）
3. 修改 `firmware/version.json`：`version` 改为新版本号，`url` 指向新 bin 文件名
4. 提交推送。设备夜间自动检查并提示更新

### 应用商店上架 / 更新

1. 在 `store/<应用名>/` 放入新版本包 `<应用名>_v<版本>.msp`
2. 在 `manifest.json` 的 `packages` 数组登记：应用 id、名称、最新版本、下载路径、大小、sha256
3. 同步更新本文件的清单表格
4. 提交推送。设备商店刷新即见

## 当前清单

### 固件

| 版本 | 文件 | 日期 |
|------|------|------|
| 1.0.0 | firmware/memoria_os_1.0.0.bin | 2026-10-01 |

### 应用商店

（暂无上架应用）
