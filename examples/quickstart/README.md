# Shepherd Quickstart Examples

These scripts are the checked-in form of the demos emitted by `sp demo write`.

```bash
python examples/quickstart/offline_task.py

mkdir /tmp/shepherd-quickstart
cd /tmp/shepherd-quickstart
sp init
sp demo write quickstart > quickstart_demo.py
python quickstart_demo.py
sp run show --latest
sp run trace --latest --events
```

`claude_readme.py` is optional and skips unless `sp doctor claude` is green.
