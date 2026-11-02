The project that your PC is memory and any cloud AI is the brain

# nmemo

**Máy của bạn là bộ nhớ, AI trên cloud là bộ não.** nmemo lưu **nguyên văn** mọi phiên làm việc với AI
vào máy bạn, và cho AI tự tra lại khi cần — để AI biết bạn đã nói gì *và chính nó đã làm gì*.

Bản thử nghiệm 0.1 chạy như một plugin của Claude Code. Thiết kế đầy đủ (nén, token, trí nhớ nhiều
tầng): [docs/kien-truc.md](docs/kien-truc.md).

## nmemo lưu gì

Mỗi lượt hỏi–đáp, hook của plugin tự ghi vào `~/.nmemo/nmemo.db` (SQLite):

| Loại | Ví dụ |
|---|---|
| Lời bạn nhắn | "Tạo file hello.py in ra…" |
| Câu trả lời của AI, cả các dòng báo tiến độ | "Xong, mình đã tạo hello.py…" |
| Mọi lệnh AI chạy, với đầu vào đầy đủ | `$ python3 hello.py`, `Write hello.py` + toàn bộ nội dung, `Edit` + đoạn cũ/mới |
| Kết quả trả về của từng lệnh | `xin chào nmemo` |
| Nội dung file sau mỗi lần AI tạo/sửa | mỗi phiên bản lưu đủ, trùng nội dung thì lưu một lần |
| File do lệnh shell tạo/sửa trong lượt đó | `output.csv` sinh ra từ một script |
| Phiên của subagent | gắn với phiên cha |

Mỗi dòng transcript gốc được giữ **đúng từng byte** kèm SHA-256 (`nmemo export` xuất lại y hệt file
gốc), nén xz theo khung 64 KB. Không có gì rời khỏi máy, trừ đoạn AI chủ động đọc qua công cụ.

AI tra lại qua MCP server `nmemo` với 5 công cụ:

| Công cụ | Việc |
|---|---|
| `sessions` | Bản đồ các phiên: thời gian, thư mục, tiêu đề, số lượt hỏi/lệnh/file |
| `search` | Tìm theo từ hoặc "cụm chính xác", **không cần gõ dấu** (`duong` ra `đường`) |
| `timeline` | Diễn biến một phiên theo thứ tự |
| `read` | Nguyên văn theo id: `i…` một mục, `e…` dòng transcript gốc, `s…` tổng quan phiên |
| `remember` | Lưu một ghi chú bền vững |

Đầu mỗi phiên, AI được nhắc ngắn (vài dòng) các phiên gần nhất ở cùng thư mục.

## Cài đặt

Cần Python 3.9+ có lệnh `python3` (chỉ dùng thư viện chuẩn, không cần `pip install`).

Trong Claude Code (bản 2.1.275 trở lên), gõ ở dấu nhắc:

```
/plugin install nmemo --marketplace dalayhoailinh/nmemo
```

Trả lời `y` để thêm marketplace, rồi chọn phạm vi (user = mọi dự án). Từ shell:

```bash
claude plugin install nmemo --marketplace dalayhoailinh/nmemo
```

Bản Claude Code cũ hơn:

```
/plugin marketplace add dalayhoailinh/nmemo
/plugin install nmemo@nmemo
```

Ứng dụng desktop (cả Cowork): bấm **+** cạnh ô nhập → **Plugins** → **Add marketplace** với
`dalayhoailinh/nmemo` → **Add plugin**. Nếu repo để private, máy cần có sẵn quyền đọc GitHub
(SSH key, hoặc `gh auth login` rồi `gh auth setup-git`).

Chạy thử không cần cài: `claude --plugin-dir /đường/dẫn/tới/nmemo`.

Lần đầu chạy, nmemo nhập nền các transcript cũ còn trong `~/.claude/projects`. Claude Code mặc
định xóa transcript sau 30 ngày (`cleanupPeriodDays`), nên cài càng sớm thì giữ được càng nhiều.

## Dùng

Cứ làm việc bình thường. Khi cần, hỏi kiểu "lần trước mình sửa file login.py thế nào?" hay
"hôm qua mình chạy lệnh gì bị lỗi?" — AI tự gọi `sessions`/`search`/`read`.

Dòng lệnh (trong thư mục repo, hoặc thư mục plugin đã cài):

```bash
python3 bin/nmemo sessions
```

```bash
python3 bin/nmemo search "duong di ngan nhat"
```

```bash
python3 bin/nmemo timeline s3
```

```bash
python3 bin/nmemo read i120
```

```bash
python3 bin/nmemo export s3 > s3.jsonl
```

```bash
python3 bin/nmemo stats
```

```bash
python3 bin/nmemo verify
```

`import [thư mục]` nhập transcript bằng tay; `remember "…"` lưu ghi chú; `compact` nén nốt phần đang chờ.

## Đã thử thật

- 12 test tự động: `python3 -m unittest discover -s tests -v`.
- Claude Code 2.1.316, chạy headless với `--plugin-dir`:
  - Phiên 1: "tạo hello.py rồi chạy". nmemo ghi đủ câu hỏi, lệnh `Write` kèm nội dung, bản chụp file,
    lệnh `python3 hello.py`, kết quả `xin chào nmemo`, câu trả lời cuối.
  - Phiên 2, ở thư mục khác, không có ngữ cảnh gì: "lần trước mình tạo file gì, nội dung chính xác,
    chạy ra gì?". AI tự gọi `sessions` → `search` → `read` và trả lời đúng `print('xin chào nmemo')`
    cùng kết quả chạy, có ghi id. Hai phiên tốn tổng cộng dưới 0.01 USD.
- Chính cuộc trò chuyện thiết kế nmemo (transcript 2.1 MB): sau nén còn 34% (phần lớn còn lại là chữ
  ký mã hóa ngẫu nhiên, không nén được); cả file SQLite kèm chỉ mục 1.1 MB; `verify` khớp 100%.

## Cowork

Cowork dùng cùng định dạng plugin nên cài theo cách ở trên. **Chưa kiểm chứng trong Cowork** — môi
trường thử chỉ có Claude Code. Khi thử, xem ba điều:

1. Hook có chạy không: sau một phiên, `nmemo sessions` có phiên đó không.
2. MCP server Python có khởi động trong máy ảo của Cowork không (AI có thấy công cụ `nmemo`).
3. `~/.nmemo` trong máy ảo có còn sau khi phiên kết thúc không. Nếu không, đặt `NMEMO_HOME` vào thư
   mục bạn đã chia sẻ cho Cowork.

## Cấu hình (biến môi trường)

| Biến | Tác dụng |
|---|---|
| `NMEMO_HOME` | Thư mục dữ liệu (mặc định `~/.nmemo`) |
| `NMEMO_CONTEXT=0` | Tắt đoạn nhắc đầu phiên |
| `NMEMO_SCAN=0` | Không quét file do lệnh shell thay đổi |
| `NMEMO_MAX_FILE_MB` | Không chụp file lớn hơn mức này (mặc định 20) |
| `NMEMO_BACKFILL=0` | Không tự nhập transcript cũ |
| `NMEMO_CODEC` | `xz` (mặc định), `zlib` hoặc `raw` |
| `NMEMO_DISABLE=1` | Tắt hẳn hook |

Lỗi của hook (nếu có) ghi vào `~/.nmemo/nmemo.log`; hook không bao giờ làm hỏng phiên làm việc.

## Riêng tư

- Kho chứa **mọi thứ** từng xuất hiện trong phiên, kể cả mật khẩu hay khóa API nếu chúng từng được gõ
  hoặc được AI đọc. File tạo ra với quyền chỉ chủ sở hữu đọc được (600); nên mã hóa ổ đĩa.
- Máy bạn là bản duy nhất: hãy sao lưu `~/.nmemo` (mã hóa trước nếu đưa lên cloud).
- Khi AI gọi `search`/`read`, đoạn được đọc đi vào ngữ cảnh của AI như mọi tin nhắn khác.

## Cấu trúc

| Đường dẫn | Nội dung |
|---|---|
| `.claude-plugin/` | `plugin.json` (khai báo MCP server), `marketplace.json` |
| `hooks/hooks.json` | 7 hook gọi `bin/nmemo hook` |
| `skills/nmemo/SKILL.md` | Hướng dẫn AI khi nào và cách dùng bộ nhớ |
| `nmemo/store.py` | Kho SQLite: dòng gốc theo khung nén, blob theo SHA-256, chỉ mục FTS5 |
| `nmemo/claude.py`, `nmemo/ingest.py` | Đọc transcript Claude Code, chụp file |
| `nmemo/hooks.py`, `nmemo/mcp.py`, `nmemo/cli.py` | Hook, MCP server (stdio), dòng lệnh |
| `bench/compress_bench.py` | Đo nén trên dữ liệu của bạn |
| `docs/kien-truc.md` | Thiết kế đầy đủ |

## Chưa có (bước tiếp)

- Tầng tóm tắt và "sự thật" (episode, fact, hồ sơ) trong thiết kế — bản này mới có tầng 0 (bản gốc)
  và chỉ mục full-text.
- Tìm theo ý nghĩa (vector).
- Nguồn khác ngoài Claude Code: Messenger, Zalo, sách PDF.
- Windows: cần lệnh `python3` trong PATH, hoặc sửa lệnh trong `hooks/hooks.json` và `.claude-plugin/plugin.json`.
