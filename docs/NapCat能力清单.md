# NapCat 能力清单（2026-11-12 实测，NapCat.Onebot v4.18.41 / OneBot v11）

> 探法：逐个点名调一次，回 `不支持的Api` 的记不支持；其余记支持（不代表参数都填对）。
> 端口 6700，`Authorization: Bearer <qq_state/qqbot_token 或 config 里的 token>`。

## 一、账号与资料（7）
`get_login_info` 我是谁 · `get_stranger_info` 查某人资料 · `get_friend_list` 好友表
`set_qq_profile` 改昵称/签名 · `set_qq_avatar` 换头像 · `set_friend_remark` 改备注 · `delete_friend` 删好友

## 二、消息（读，9）
`get_friend_msg_history` 私聊历史 · `get_group_msg_history` 群历史 · `get_msg` 按 id 取一条
`get_forward_msg` 取合并转发内容 · `get_image` 取图 · `get_record` 取语音 · `get_file` 取文件
`get_recent_contact` 最近会话 · `mark_msg_as_read` 标已读

## 三、消息（写，6）
`send_private_msg` · `send_group_msg` · `send_private_forward_msg` · `send_group_forward_msg`
`send_like` 名片点赞 · `send_group_sign` 群签到
（消息里能带 CQ 码：`[CQ:reply]` 引用、`[CQ:image]` 图、`[CQ:face]` 表情、`[CQ:at]` @、`[CQ:record]` 语音、`[CQ:file]` 文件）

## 四、群（读，10）
`get_group_list` · `get_group_info` · `get_group_member_list` · `get_group_member_info`
`get_group_system_msg` 群系统消息 · `get_group_at_all_remain` 剩余 @全体次数 · `get_group_honor_info` 群荣誉
`get_group_shut_list` 禁言名单 · `get_essence_msg_list` 精华消息 · `get_group_ignore_add_request`

## 五、群文件（4，用户点名要的）
`get_group_root_files` 根目录列表 · `get_group_files_by_folder` 子目录 · `get_group_file_url` 拿下载地址
`get_private_file_url` 私聊文件地址
配套：`upload_group_file` / `upload_private_file` / `download_file` 上传与下载

## 六、群管理（写，9）
`set_group_name` · `set_group_card` 改群名片 · `set_group_special_title` 头衔 · `set_group_portrait` 群头像
`set_group_ban` 单人禁言 · `set_group_whole_ban` 全员禁言 · `set_group_kick` 踢人
`set_essence_msg` / `delete_essence_msg` 设/撤精华 · `set_group_leave` 退群

## 七、凭证（3，谨慎）
`get_cookies` · `get_csrf_token` · `get_credentials` —— 这些是 QQ 网页侧的钥匙，能做 OneBot 之外的
事（比如空间），但**等于把号交出去**，只在本地、只在必要时用，不写日志、不外传。

## 八、明确不支持（15）
`get_api_list` `get_group_file_list` `get_group_notice` `get_unread_friend_count` `get_recent_contact_v2`
`get_group_add_request` `get_friend_add_request` `get_model_show` `get_supported_actions`
`get_qzone_like` `get_qzone_list` `get_qq_avatar` `get_avatar` `get_user_status`
`get_group_list_by_owner` `set_group_anonymous_ban`

## 九、结论（对我们有用的）
- **说说/空间没有 OneBot 接口**：要发说说只能借 `get_cookies` 走空间网页接口，风险与维护成本都高，先记着、不急。
- **群文件能完整做**：列表 → 拿 url → 下载 → 记「谁发的、叫什么、多大」，本地存 `qq_state\files\`。
- **引用回复、@、表情、图片、语音都靠 CQ 码**，已经能在 `tools/qq_send.py` 里带引用。
- **已读/未读**：`mark_msg_as_read` 能把我这边的未读清掉；"对方读没读"拿不到。
