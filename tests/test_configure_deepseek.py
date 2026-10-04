from pathlib import Path
import stat
import pytest
from scripts.configure_deepseek import configure


def prompts(values):
    it=iter(values)
    return lambda label:next(it)


def test_hidden_setup_preserves_other_settings_and_private_permissions(tmp_path,capsys):
    folder=tmp_path/'.daytrace';folder.mkdir()
    p=folder/'secrets.env';p.write_text('# retain this\nOTHER_SETTING=retained\nDEEPSEEK_API_KEY=\n')
    assert configure(tmp_path,prompts(['sk-fake-test-only','sk-fake-test-only']))
    assert p.read_text()=='# retain this\nOTHER_SETTING=retained\nDEEPSEEK_API_KEY=sk-fake-test-only\n'
    assert stat.S_IMODE(p.stat().st_mode)==0o600
    assert stat.S_IMODE(folder.stat().st_mode)==0o700
    assert 'sk-fake' not in capsys.readouterr().out
    assert not list(folder.glob('.secrets-*'))


def test_existing_key_not_shown_or_overwritten(tmp_path,capsys):
    folder=tmp_path/'.daytrace';folder.mkdir()
    p=folder/'secrets.env';prior='DEEPSEEK_API_KEY=sk-existing-fake\nOTHER=x\n';p.write_text(prior)
    assert not configure(tmp_path,lambda _:pytest.fail('Must not ask to overwrite existing key'))
    assert p.read_text()==prior
    assert 'sk-existing' not in capsys.readouterr().out


def test_mismatch_or_newline_never_changes_existing_file(tmp_path):
    folder=tmp_path/'.daytrace';folder.mkdir()
    p=folder/'secrets.env';p.write_text('OTHER=x\n')
    with pytest.raises(ValueError):configure(tmp_path,prompts(['sk-one','sk-two']))
    assert p.read_text()=='OTHER=x\n'
    with pytest.raises(ValueError):configure(tmp_path,prompts(['sk-key\nEXTRA=bad']))
    assert p.read_text()=='OTHER=x\n'


def test_symlink_target_rejected_without_modification(tmp_path):
    folder=tmp_path/'.daytrace';folder.mkdir()
    other=tmp_path/'other';other.write_text('retained')
    (folder/'secrets.env').symlink_to(other)
    with pytest.raises(ValueError):configure(tmp_path,prompts(['fake','fake']))
    assert other.read_text()=='retained'
