"""SSH/remote execution helpers used by connectors and runners."""

import base64
import shlex
import socket
import time
from pathlib import Path

import paramiko

import src.classes.target as t


def get_file_from_path(config_path: str | Path, config_ssh: str = None) -> bytes:
    """Fetch the content of a file on this machine or via SSH, returning bytes.

    :param config_path: path to the file
    :param config_ssh: SSH target of format user@ip; if None, reads locally
    """
    if config_ssh is None:
        if not Path(config_path).exists():
            raise FileNotFoundError(f"File not found: {config_path}")
        with open(config_path, "rb") as f:
            return f.read()

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
                return f.read()
    finally:
        client.close()


def get_file_from_url(url: str) -> bytes:
    """Fetch the content at a URL and return it as bytes."""
    import httpx
    response = httpx.get(url, follow_redirects=True)
    response.raise_for_status()
    return response.content


def execute_on_machine(config_ssh: str, command: str, return_error: bool = False) -> str | tuple[str, str, int]:
    """Execute a shell command on a remote machine via SSH.

    :param config_ssh: SSH target of format user@ip
    :param command: bash command to run
    :param return_error: if True, return (stdout, stderr, exit_code) instead of raising on error
    """
    user, host = config_ssh.split("@")
    client = paramiko.SSHClient()
    client.load_system_host_keys()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(host, username=user, timeout=30)
        _, stdout, stderr = client.exec_command(command, timeout=300)
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
        # Can happen with password-only auth when no password is provided
        raise e
    finally:
        client.close()


def execute_on_ct(target: t.ProxmoxCT, command: str) -> tuple[str, str, int, float]:
    """Execute a command inside a Proxmox LXC container, dispatching by OS type."""
    node_ssh = target.ssh_addr
    pct_id = target.pct_id
    ostype = target.ostype

    start = time.time()
    match ostype:
        case "ubuntu":
            stdout_str, stderr_str, exit_code = execute_on_ubuntu_ct(node_ssh, pct_id, command)
        case "debian":
            stdout_str, stderr_str, exit_code = execute_on_debian_ct(node_ssh, pct_id, command)
        case _:
            raise NotImplementedError("Proxmox CT execution not implemented for os", ostype)
    return stdout_str, stderr_str, exit_code, time.time() - start


def execute_on_debian_ct(node_ssh: str, pct_id: int, command: str) -> tuple[str, str, int]:
    token = "##CMD_OUTPUT_START##"
    inner = f"echo '{token}'; {command}"
    exec_cmd = f"pct exec {pct_id} -- bash -lc {shlex.quote(inner)}"
    return _execute_helper(node_ssh, pct_id, command, exec_cmd=exec_cmd, token=token)


def execute_on_ubuntu_ct(node_ssh: str, pct_id: int, command: str) -> tuple[str, str, int]:
    # Ubuntu LXC containers require `su -l root` instead of `bash -lc` for a login shell
    token = "##CMD_OUTPUT_START##"
    inner = f"echo '{token}'; {command}"
    exec_cmd = f"pct exec {pct_id} -- su -l root -c {shlex.quote(inner)}"
    return _execute_helper(node_ssh, pct_id, command, exec_cmd=exec_cmd, token=token)


def _execute_helper(
        node_ssh: str,
        pct_id: int,
        command: str,
        token: str,
        exec_cmd: str,
        timeout: int = 60,
) -> tuple[str, str, int]:
    """SSH into the Proxmox node, run exec_cmd, then strip the sentinel token from stdout."""
    user, host = node_ssh.split("@")
    client = paramiko.SSHClient()
    client.load_system_host_keys()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(host, username=user, timeout=timeout)
        _, stdout, stderr = client.exec_command(exec_cmd, timeout=timeout)
        stderr_str = stderr.read().decode()
        stdout_str = stdout.read().decode()
        if token in stdout_str:
            stdout_str = stdout_str.split(token, 1)[1].lstrip("\n")
        exit_code = stdout.channel.recv_exit_status()
        return stdout_str, stderr_str, exit_code
    except socket.timeout:
        raise RuntimeError(f"Timeout executing `{command}` on CT {pct_id} via {node_ssh}")
    except paramiko.ssh_exception.AuthenticationException as e:
        # Can happen with password-only auth when no password is provided
        raise e
    finally:
        client.close()


def execute_script_on_ct(target: t.ProxmoxCT, script_path: Path) -> tuple[str, str, int, float]:
    """Base64-encode a local script and pipe it into bash inside the container."""
    script_b64 = base64.b64encode(script_path.read_bytes()).decode("ascii")
    command = f"echo {shlex.quote(script_b64)} | base64 -d | bash"
    return execute_on_ct(target, command)


def execute_on_linux(target: t.RemoteLinuxMachine, command: str, timeout: int = 60) -> tuple[str, str, int, float]:
    """Execute a command on a remote Linux machine via SSH."""
    client = paramiko.SSHClient()
    client.load_system_host_keys()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(str(target.machine_ip), username=target.user, timeout=timeout)
        start = time.time()
        _, stdout, stderr = client.exec_command(f"bash -lc {shlex.quote(command)}", timeout=timeout)
        stderr_str = stderr.read().decode()
        stdout_str = stdout.read().decode()
        exit_code = stdout.channel.recv_exit_status()
        duration = time.time() - start
        return stdout_str, stderr_str, exit_code, duration
    except socket.timeout:
        raise RuntimeError(f"Timeout executing `{command}` on machine {target.hostname} via {target.ssh_addr}")
    except paramiko.ssh_exception.AuthenticationException as e:
        # Can happen with password-only auth when no password is provided
        raise e
    finally:
        client.close()


def execute_script_on_linux(target: t.RemoteLinuxMachine, script_path: Path) -> tuple[str, str, int, float]:
    """Base64-encode a local script and pipe it into bash on the remote machine."""
    script_b64 = base64.b64encode(script_path.read_bytes()).decode("ascii")
    command = f"echo {shlex.quote(script_b64)} | base64 -d | bash -l"
    return execute_on_linux(target, command)
