# Tiến độ — nmemo

Cập nhật: 2026-11-02

## Bối cảnh

- Project là gì: plugin Claude Code lưu nguyên văn mọi phiên (tin nhắn, câu trả lời, lệnh, kết quả,
  file) vào SQLite ở `~/.nmemo`, cho AI tra lại qua MCP, tự nhớ lại theo từng tin nhắn và tiêu hóa
  sau phiên bằng model rẻ. Thiết kế: `docs/kien-truc.md`.
- Cho ai: chủ repo, dùng hằng ngày với Claude Code (app desktop và dòng lệnh) trên Windows 11.
- Ràng buộc: chỉ thư viện chuẩn của Python. Mọi thứ gọi model (tiêu hóa) phải được chủ repo đồng ý
  trước vì tốn tiền hoặc hạn mức.
- Bẫy đã biết:
  - Plugin cài từ marketplace thư mục `D:\dev\nmemo`, nên hook và MCP chạy thẳng từ repo
    (`D:/dev/nmemo/bin/nmemo`), không chạy bản copy trong `~/.claude/plugins/cache`. Đổi nhánh git
    là đổi bản đang chạy.
  - Trên Windows, `write_text` ghi `\r\n`; test so byte phải đọc bằng `read_bytes`. Nếu so sai,
    `difflib` in diff rất lâu, trông như test bị treo.
  - Máy này để `LongPathsEnabled=0`: mở đường dẫn từ 260 ký tự phải đi qua `ingest.fs_path`.
    Transcript trong scratch workspace của app desktop đã dài tới 255 ký tự.
  - `NMEMO_DIGEST=0` nằm trong `env` của `~/.claude/settings.json`, nên cũng có trong môi trường
    của chính session Claude Code. Muốn thử tiêu hóa thì phải bỏ biến này khỏi môi trường thử.
  - Lần nhập đầu với nhiều transcript giữ khóa ghi, nên hook của phiên đang chạy phải chờ (đo được
    17 giây với 59 MB). Kho thật đã nhập sẵn nên không gặp lại.
  - Ba file `subagents/workflows/*/journal.jsonl` bị nhập thành phiên rỗng (`?`, 0 lượt hỏi). Chúng
    không có timestamp nên lấy giờ nhập làm giờ phiên và đứng đầu danh sách phiên.

## Chặng đường

- 2026-11-02 · nmemo 0.1 chạy trên máy Windows của chủ repo · hook, MCP, tự nhớ lại, nhập nền,
  tiêu hóa đã kiểm bằng Claude Code thật với API giả

## Mục tiêu hiện tại

Dùng nmemo hằng ngày trên máy này · XONG khi: một phiên mới trong app desktop tự vào
`~/.nmemo` và AI tra được phiên cũ · Hạn: không đặt

## Đang làm

- Việc: cài nmemo lên máy Windows.
- Đã tới đâu: đã cài ở phạm vi user, đã nhập 155 transcript cũ, tiêu hóa đang tắt chờ quyết định.
- Bước kế tiếp: mở một phiên mới trong app, gửi vài tin, rồi chạy
  `python3 D:\dev\nmemo\bin\nmemo sessions --limit 3` để xem phiên đó đã vào kho.
- Trạng thái kiểm chứng: 23 test xanh. Claude Code 2.1.309 và 2.1.282 chạy với API giả ở máy.
  Chưa chạy phiên nào với model thật kể từ khi cài.

## Hàng đợi

1. Quyết định có bật tiêu hóa không (xóa `NMEMO_DIGEST` khỏi `~/.claude/settings.json`).
2. Commit phần sửa: test CRLF, `fs_path`, README, file này.
3. (nhỏ) Bỏ qua `journal.jsonl` của workflow khi nhập. Bắt thêm câu "Output has been saved to" mà
   MCP dùng khi kết quả quá dài (hiện chỉ bắt "Full output saved to").

## Vướng / câu hỏi mở

- Có bật tiêu hóa sau phiên không? Bật thì mỗi phiên tốn một lượt `claude -p` với Haiku.

## Quyết định

- 2026-11-02 · Cài từ marketplace thư mục `D:\dev\nmemo`, không cài từ GitHub · Tại sao: chạy đúng
  bản trong máy, kể cả phần sửa chưa commit, không cần mạng hay quyền GitHub · Thay thế đã bỏ:
  `claude plugin install nmemo --marketplace dalayhoailinh/nmemo` (phải push trước)
- 2026-11-02 · Tắt tiêu hóa bằng `NMEMO_DIGEST=0` trong `~/.claude/settings.json` · Tại sao: gọi
  model phải hỏi trước · Thay thế đã bỏ: để mặc định bật
- 2026-11-02 · Kiểm chứng bằng Claude Code thật trỏ `ANTHROPIC_BASE_URL` vào một API giả ở máy, với
  `CLAUDE_CONFIG_DIR` và `NMEMO_HOME` riêng · Tại sao: thấy hook, MCP và tiến trình nền chạy thật mà
  không tốn token · Thay thế đã bỏ: `claude -p` với model thật
- 2026-11-02 · Thêm tiền tố `\\?\` cho đường dẫn dài (`ingest.fs_path`) · Tại sao: không cần quyền
  admin và đúng trên mọi máy Windows · Thay thế đã bỏ: bật `LongPathsEnabled` trong registry

## Đã xong

- 2026-11-02 · Cài plugin, nhập 155 transcript (379 MB còn 133 MB, 104 giây) · `verify` khớp
  SHA-256 cho toàn bộ hơn 72 nghìn bản ghi; hook mất 0,2–0,4 giây trên kho thật
- 2026-11-02 · Đọc được transcript và file có đường dẫn từ 260 ký tự · test mới xanh; phiên ở thư mục
  dài lưu được 39 dòng (trước khi sửa: 0)
- 2026-11-02 · Sửa 2 test hỏng trên Windows (so `\r\n` với `\n`) · 22/22 xanh
