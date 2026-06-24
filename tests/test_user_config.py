from omegaconf import OmegaConf

from zotero_arxiv_daily.user_config import RuntimeArgs, apply_runtime_config


def test_apply_runtime_config_loads_user_and_test_range(config, tmp_path):
    (tmp_path / "users.yaml").write_text(
        """
users:
  liruoyi:
    zotero:
      user_id: 16986970
      api_key: zotero-key
    email:
      receiver: lxr@example.com
    profile:
      search_queries: [fully homomorphic encryption]
      keywords: [fully homomorphic, lwe]
    monthly:
      ccf_fields: [网络与信息安全]
      ccf_ranks: [A]
""",
        encoding="utf-8",
    )

    updated = apply_runtime_config(
        config,
        RuntimeArgs(
            user="liruoyi",
            mode="test-range",
            start_date="2026-05-01",
            end_date="2026-07-31",
            send_email=True,
        ),
        root=tmp_path,
    )

    assert updated.zotero.user_id == 16986970
    assert updated.zotero.api_key == "zotero-key"
    assert updated.email.receiver == "lxr@example.com"
    assert list(updated.executor.source) == ["ccf_crossref"]
    assert updated.source.ccf_crossref.start_date == "2026-05-01"
    assert updated.source.ccf_crossref.end_date == "2026-07-31"
    assert updated.source.ccf_crossref.fields == ["网络与信息安全"]
    assert updated.source.ccf_crossref.ranks == ["A"]
    assert updated.state.ignore_seen is True
    assert updated.executor.send_empty is True


def test_apply_runtime_config_daily_mode(config, tmp_path):
    (tmp_path / "users.yaml").write_text(
        """
users:
  liruoyi:
    zotero:
      user_id: 16986970
      api_key: zotero-key
    email:
      receiver: lxr@example.com
""",
        encoding="utf-8",
    )

    updated = apply_runtime_config(config, RuntimeArgs(user="liruoyi", mode="daily"), root=tmp_path)

    assert list(updated.executor.source) == ["arxiv", "iacr_eprint"]
    assert list(updated.source.arxiv.category) == ["cs.CR", "cs.DS", "cs.IT"]
    assert updated.source.arxiv.include_cross_list is True
    assert updated.state.enabled is True
    assert updated.state.ignore_seen is False
