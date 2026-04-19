from enum import Enum


class ConnectorType(str, Enum):
    proxmox = "Proxmox"
    caddy = "Caddy"
    linux_machine = "Linux Remote Machine"


class RunnerType(str, Enum):
    proxmox_ct = "proxmox_ct"
    linux_machine = "linux_machine"
    web = "web"


class ExecMethod(str, Enum):
    command = "command"
    script = "script"  # exec is a path relative to SCRIPTS_FOLDER; the runner decides where to run it

class CheckMethod(str, Enum):
    exit_code = "exit_code"
    stdout_regex = "stdout_regex"
    stderr_empty = "stderr_empty"
    stdout_contains = "stdout_contains"
    stdout_not_empty = "stdout_not_empty"
    finish_in_less_than = "finish_in_less_than"

    def requires_pattern(self):
        return self in (CheckMethod.stdout_contains, CheckMethod.stdout_regex, CheckMethod.finish_in_less_than)
