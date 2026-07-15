"""adapter/archive_recall.py — 触发原因：归档 / 召回。

职责：
    - on_archive(character_name, messages, summary_entry, visible_msgs, *, physical_total)：
        重写块2（合并摘要 + 渲染 filtered recent）+ 更新 _dump_meta.written_len
    - on_recall(character_name, topic_id, recall_block)：
        recall_topic 的真实 tool result 已在 messages 列表的 tool msg 中，
        dump 阶段会自然渲染到"## 近期对话原文"，本函数为 noop，
        保留接口是为了让调用方统一走适配层。

调用方：
    - tool/builtin_tools/experience.py:archive_recent_talk
    - tool/builtin_tools/experience.py:recall_topic

设计原则：
    - summary 合并/去重逻辑只在这里出现，writer 层只负责"写"
    - physical_total 必须从 history.json 物理长度读取（或由调用方传入），
      避免 archive 后 written_len 错位导致 dump 重复追加
"""
from __future__ import annotations


def on_archive(character_name: str, messages: list[dict],
        summary_entry: dict, visible_msgs: list[dict] | None = None,
        *, physical_total: int | None = None) -> dict:
    """归档：重写块2 + 更新 _dump_meta.written_len。

    等价于 update_experience("archive", {"messages", "summary_entry", "visible_msgs", "physical_total"})

    参数：
        messages: history.json 的全部消息（用于推断 physical_total）
        summary_entry: 本次归档的摘要 dict（会与现有 entries 合并或追加）
        visible_msgs: 已过滤（_gaps_between_covered）的近期对话原文
                      若为 None，自动取 messages[3:]
        physical_total: history.json 物理总消息数；None 时从磁盘读

    返回：更新后的 _dump_meta 字典
    """
    from experience.io.writer import write_block2_rewrite
    from .conversation import _render_messages_to_recent_section

    # 渲染 visible_msgs（archive_recent_talk 已用 _gaps_between_covered 过滤）
    dialogue_msgs = visible_msgs
    if dialogue_msgs is None:
        dialogue_msgs = messages[3:] if len(messages) > 3 else []
    new_recent = _render_messages_to_recent_section(dialogue_msgs)

    return write_block2_rewrite(
        character_name,
        summary_entry=summary_entry,
        recent_text=new_recent,
        physical_total=physical_total,
        messages=messages,
    )


def on_recall(character_name: str, topic_id: str, recall_block: str) -> None:
    """召回：no-op。

    recall_topic 的真实 tool result (recall_block) 已在 messages 列表的 tool msg 中，
    dump 阶段会原样渲染到"## 近期对话原文"段。
    这里保留接口仅为调用方统一走适配层。
    """
    return None


__all__ = ["on_archive", "on_recall"]