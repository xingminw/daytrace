#!/usr/bin/env python3
"""Interactive local key setup. No command-line key, echo, network or AI enablement."""
from __future__ import annotations
import fcntl
import getpass
import os
from pathlib import Path
import stat
import sys
import tempfile
import warnings

NAME = 'DEEPSEEK_API_KEY'


def configured(text: str) -> bool:
    for line in text.splitlines():
        if line.lstrip().startswith('#') or '=' not in line:
            continue
        name,value=line.split('=',1)
        if name.strip()==NAME and value.strip():
            return True
    return False


def configure(home: Path, prompt=None) -> bool:
    """Return True only when a previously absent/empty key was saved.

    The injectable prompt is for offline tests; the CLI accepts no key argument.
    """
    prompt=prompt or getpass.getpass
    directory=home/'.daytrace'
    target=directory/'secrets.env'
    if directory.is_symlink() or target.is_symlink():
        raise ValueError('配置路径不能是符号链接；未写入任何密钥。')
    directory.mkdir(mode=0o700,parents=True,exist_ok=True)
    if directory.stat().st_uid!=os.getuid():
        raise ValueError('配置目录不属于当前用户；未写入。')
    directory.chmod(0o700)
    lock_path=directory/'.configure.lock'
    fd=os.open(lock_path,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        if target.exists():
            info=target.stat()
            if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid():
                raise ValueError('配置文件不是当前用户的普通文件；未写入。')
            original=target.read_text(encoding='utf-8')
        else:
            original=''
        if configured(original):
            target.chmod(0o600)
            print('DeepSeek 密钥配置已存在，未显示、未覆盖。')
            return False
        with warnings.catch_warnings():
            warnings.simplefilter('error',getpass.GetPassWarning)
            key=prompt('请输入 DeepSeek API Key（隐藏输入，不会显示）：').strip()
            if not key or any(ch.isspace() or ch=='\x00' for ch in key):
                raise ValueError('密钥为空或含空白字符；未保存。')
            confirmation=prompt('请再次输入同一密钥确认（隐藏输入）：').strip()
        if key!=confirmation:
            raise ValueError('两次输入不一致；未保存。')
        # Keep every unrelated setting/comment exactly as it was.
        lines=original.splitlines(keepends=True)
        replaced=False
        for i,line in enumerate(lines):
            if not line.lstrip().startswith('#') and '=' in line and line.split('=',1)[0].strip()==NAME:
                if not replaced:
                    lines[i]=NAME+'='+key+'\n';replaced=True
                else:
                    # Remove only redundant empty entries for this same key.
                    lines[i]=''
        updated=''.join(lines)
        if not replaced:
            if updated and not updated.endswith('\n'):updated+='\n'
            updated+=NAME+'='+key+'\n'
        temporary=None
        try:
            with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=directory,prefix='.secrets-',delete=False) as out:
                temporary=Path(out.name)
                os.chmod(temporary,0o600)
                out.write(updated);out.flush();os.fsync(out.fileno())
            os.replace(temporary,target)
            temporary=None
        finally:
            if temporary is not None:temporary.unlink(missing_ok=True)
        print('已保存到 ~/.daytrace/secrets.env，权限仅限当前用户（600）。')
        return True


def main() -> int:
    if len(sys.argv)!=1:
        print('请直接运行本脚本；不要把密钥放到命令参数中。',file=sys.stderr)
        return 2
    if not sys.stdin.isatty():
        print('请在你自己的交互终端运行；为避免回显，脚本不接受管道或重定向输入。',file=sys.stderr)
        return 2
    try:
        configure(Path.home())
    except (ValueError,OSError,UnicodeError,getpass.GetPassWarning):
        print('配置未完成：请确认配置路径属于当前用户、输入为单行且两次一致。未输出密钥。',file=sys.stderr)
        return 1
    except (KeyboardInterrupt,EOFError):
        print('\n已取消，未保存新密钥。',file=sys.stderr)
        return 1
    print('没有联网验证或调用 API；AI 与定时任务仍未启用。现在不需要重启网站。')
    print('后续确认模型、回补范围和预算后，由维护流程统一加载此文件；无需再改其他配置。')
    return 0


if __name__=='__main__':
    raise SystemExit(main())
