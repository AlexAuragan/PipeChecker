import shlex
import socket
from pathlib import Path

import paramiko

from src.classes import target as t


class IP(str):
    def __init__(self, value):
        assert value
        super().__init__()

def get_file_from_path(config_path: str | Path, config_ssh: str = None) -> bytes:
    """
    fetch the content of the file at `config_path` on this machine or at `config_ssh` and return it as bytes
    :param config_path: path to the config
    :param config_ssh: of format user@ip
    :return:
    """
    if config_ssh is None:
        if not Path(config_path).exists():
            raise FileNotFoundError(f"File not found: {config_path}")
        with open(config_path, "rb") as f:
            out = f.read()
        return out
    else:
        user, host = config_ssh.split("@")
        client = paramiko.SSHClient()
        client.load_system_host_keys()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy)
        try:
            client.connect(host, username=user, timeout=30)
            with client.open_sftp() as sftp:
                try:
                    sftp.stat(str(config_path))
                except FileNotFoundError:
                    raise FileNotFoundError(f"Remote file not found: {config_path}")
                with sftp.open(str(config_path), "rb") as f:
                    out: bytes = f.read()
                return out
        finally:
            client.close()
    raise RuntimeError("couldn't fetch the file", config_path, config_ssh)

def get_file_from_url(url: str) -> bytes:
    """
    fetch the content on the file in url and return it as bytes
    :param url:
    :return:
    """
    import httpx
    response = httpx.get(url, follow_redirects=True)
    response.raise_for_status()
    return response.content

def execute_on_machine(config_ssh: str, command: str, return_error: bool = False) -> str | tuple[str, str, int]:
    """
    execute `command` on the machine `config_ssh` via ssh.
    :param config_ssh: of format user@ip
    :param command: any bash command:
    :param return_error: Whether to return the stderr and exit code
    :return: stdout, stderr
    """
    user, host = config_ssh.split("@")
    client = paramiko.SSHClient()
    client.load_system_host_keys()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(host, username=user, timeout=30)
        _, stdout, stderr = client.exec_command(command, timeout=60)
        stderr_str = stderr.read().decode()
        stdout_str = stdout.read().decode()
        exit_code = stdout.channel.recv_exit_status()
        if return_error:
            return stdout_str, stderr_str, exit_code
        if stderr_str or exit_code:
            raise RuntimeError(f"Error while executing `{command}` on remote {config_ssh}", stderr_str)
        return stdout_str
    except socket.timeout:
        raise RuntimeError(f"Timeout executing `{command}` on remote {config_ssh}")
    except paramiko.ssh_exception.AuthenticationException as e:
        print("Can happen for password connection") # we could just have a popup for the user here
        raise e
    finally:
        client.close()

def execute_on_ct(target: t.ProxmoxCT, command: str, return_error: bool = False) -> str | tuple[str, str, int]:
    node_ssh = target.ssh_addr
    pct_id = target.pct_id
    ostype = target.ostype

    match ostype:
        case "ubuntu":
            return execute_on_ubuntu_ct(node_ssh, pct_id, command, return_error)
        case "debian":
            return execute_on_debian_ct(node_ssh, pct_id, command, return_error)
        case _:
            raise NotImplementedError("Proxmox CT execution not implemented for os", ostype)

def execute_on_debian_ct(node_ssh: str, pct_id: int, command: str, return_error: bool = False) -> str | tuple[str, str, int]:
    token = "##CMD_OUTPUT_START##"
    inner = f"echo '{token}'; {command}"
    exec = f"pct exec {pct_id} -- bash -lc {shlex.quote(inner)}"
    return _execute_helper(node_ssh, pct_id, command, return_error, exec=exec, token=token)

def execute_on_ubuntu_ct(node_ssh: str, pct_id: int, command: str, return_error: bool = False) -> str | tuple[str, str, int]:
    token = "##CMD_OUTPUT_START##"
    inner = f"echo '{token}'; {command}"
    exec = f"pct exec {pct_id} -- su -l root -c {shlex.quote(inner)}"
    return _execute_helper(node_ssh, pct_id, command, return_error, exec=exec, token=token)

def _execute_helper(
        node_ssh: str, pct_id: int, command: str, return_error: bool, token: str,
        exec: str,
        timeout: int = 60
    ) -> str | tuple[str, str, int]:
    user, host = node_ssh.split("@")
    client = paramiko.SSHClient()
    client.load_system_host_keys()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(host, username=user)
        _, stdout, stderr = client.exec_command(exec, timeout=timeout
        )
        stderr_str = stderr.read().decode()
        stdout_str = stdout.read().decode()
        if token in stdout_str:
            stdout_str = stdout_str.split(token, 1)[1].lstrip("\n")
        exit_code = stdout.channel.recv_exit_status()
        if return_error:
            return stdout_str, stderr_str, exit_code
        if stderr_str or exit_code:
            raise RuntimeError(f"Error while executing `{command}` on CT {pct_id} via {node_ssh}. Is the command you ran interactive ?", stderr_str)
        return stdout_str
    except socket.timeout:
        raise RuntimeError(f"Timeout executing `{command}` on CT {pct_id} via {node_ssh}")
    except paramiko.ssh_exception.AuthenticationException as e:
        print("Can happen for password connection")
        raise e
    finally:
        client.close()


if __name__ == '__main__':
    pass
    # out = get_file_from_path("/etc/caddy/Caddyfile", "root@192.168.1.111")
    # out = get_file_from_url("https://auragan.fr/files/CV_Alexandre_DANG_catppuccin.pdf")
    # print(out)