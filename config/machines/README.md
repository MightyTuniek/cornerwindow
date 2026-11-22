# Machine files

Put per-PC tuning here as `<anything>.toml`. A file applies when its `[match]` table
matches this PC's hostname or a connected monitor's model. These files are ignored by
git, so they stay on the machine they were written for.

```toml
[match]
hostname = ["my-desktop"]
monitor_model = ["VG240Y"]

[display]
i2c_bus = 4
limits = { brightness = 30, contrast = 30 }
```
