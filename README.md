The project that your PC is memory and any cloud AI is the brain

# nmemo

**Máy của bạn là bộ nhớ, AI trên cloud là bộ não.** Với một agent làm việc trên máy bạn (hiện là
Claude Code), nmemo làm ba việc tự động, không cần nhắc:

1. **Tự lưu** nguyên văn mọi thứ bạn gửi và mọi thứ AI trả lời, chạy, tạo ra.
2. **Tự nhớ lại**: mỗi tin nhắn mới, máy tự tìm trong kho và đưa cho AI những gì liên quan.
3. **Tự tiêu hóa**: sau mỗi phiên, tóm tắt phiên đó và cập nhật một hồ sơ về bạn, để AI hiểu bạn
   ngay từ câu đầu tiên của phiên sau.

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
| File do lệnh shell tạo/sửa trong thư mục làm việc, trong lượt đó | `output.csv` sinh ra từ một script |
| Kết quả rất dài mà Claude Code cất ra file riêng | lưu đủ, không chỉ 2 KB xem trước |
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

## AI dùng kho này để nghĩ thế nào

| Lúc | Máy tự đưa cho AI | Để làm gì |
|---|---|---|
| Đầu mỗi phiên | Hồ sơ về bạn (`~/.nmemo/profile.md`) + các phiên gần đây ở thư mục này | AI biết bạn là ai, đang làm gì, thích làm việc ra sao |
| Mỗi tin nhắn | Tối đa 5 dòng liên quan nhất từ các phiên trước, ưu tiên bản tóm tắt và "sự thật" về bạn; không lặp lại trong một phiên; không có gì liên quan thì không thêm gì | Nối tiếp việc cũ mà bạn không phải kể lại |
| Khi cần chi tiết | AI tự gọi `search`/`read`: nguyên văn có id, xem được cả ảnh bạn từng gửi | Trích đúng từng chữ, không đoán |
| Sau mỗi phiên | Một model rẻ chạy ngầm viết tóm tắt (đã làm gì, quyết định gì, còn dở gì), rút ra sự thật về bạn, cập nhật hồ sơ | Lần sau AI hiểu bạn hơn |

Hồ sơ là file chữ thường, bạn mở ra sửa được, và lần cập nhật sau giữ nguyên chỗ bạn sửa. Mọi dòng
trong tóm tắt và hồ sơ đều kèm id bằng chứng; bản gốc không bao giờ bị thay.

## Có tốn token không?

- **Ghi: 0 token.** Claude Code vốn đã ghi mỗi phiên ra file trên máy; hook của nmemo chép và nén file
  đó bằng CPU của máy bạn. AI không đọc lại, không xử lý lại, không gửi lại gì để lưu.
- **Đầu phiên: khoảng 400 token + hồ sơ** (tối đa 2.500 ký tự, cỡ 700–1.000 token), được cache.
  Định nghĩa đầy đủ của các công cụ chỉ được nạp khi AI thật sự cần tra.
- **Mỗi tin nhắn: 0 token nếu không có gì liên quan**, tối đa khoảng 400–500 token nếu có.
- **Đọc thêm: chỉ khi cần, và chỉ đoạn cần.**
- **Tiêu hóa sau phiên:** chạy ngầm bằng `claude -p` với model Haiku, không công cụ, không lưu phiên,
  dùng luôn tài khoản Claude bạn đang có (không cần API key). Đo thật: tóm tắt 3 phiên + viết hồ sơ hết
  khoảng 0.004 USD. Chỉ tóm tắt phiên có việc thật (≥ 2 câu hỏi hoặc ≥ 3 lệnh) trong 7 ngày gần nhất,
  tối đa 5 phiên mỗi lượt. Tắt bằng `NMEMO_DIGEST=0`.

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

Để AI dùng bộ nhớ mà không hỏi quyền mỗi lần, chọn "Yes, don't ask again" ở lần đầu, hoặc thêm vào
`~/.claude/settings.json`:

```json
{ "permissions": { "allow": ["mcp__plugin_nmemo_nmemo"] } }
```

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

```bash
python3 bin/nmemo profile
```

`import [thư mục]` nhập transcript bằng tay; `digest` tóm tắt ngay (thêm `--all` cho mọi phiên cũ,
`--dry-run` để xem trước thứ sẽ gửi cho model); `remember "…"` lưu ghi chú; `compact` nén nốt phần đang chờ.

## Đã thử thật

- 23 test tự động: `python3 -m unittest discover -s tests -v`.
- Claude Code 2.1.316, chạy headless với `--plugin-dir`:
  - Phiên 1: "tạo hello.py rồi chạy". nmemo ghi đủ câu hỏi, lệnh `Write` kèm nội dung, bản chụp file,
    lệnh `python3 hello.py`, kết quả `xin chào nmemo`, câu trả lời cuối.
  - Phiên 2, ở thư mục khác, không có ngữ cảnh gì: "lần trước mình tạo file gì, nội dung chính xác,
    chạy ra gì?". AI tự gọi `sessions` → `search` → `read` và trả lời đúng `print('xin chào nmemo')`
    cùng kết quả chạy, có ghi id. Hai phiên tốn tổng cộng dưới 0.01 USD.
  - Phiên 3–4, không nhắc gì tới bộ nhớ: "Chạy lại giúp mình cái file in lời chào hôm trước nhé."
    Phần tự nhớ lại đưa cho AI dòng `file của s1: …/proj/hello.py`; AI đọc, chạy và ra đúng `xin chào nmemo`.
  - Tiêu hóa thật 3 phiên bằng Haiku: 0.0036 USD, ra tóm tắt từng phiên và hồ sơ đầu tiên.
  - Phiên 5: "Bạn biết gì về mình, lần trước đang làm dở gì?". AI trả lời từ hồ sơ (dùng tiếng Việt,
    thích câu trả lời ngắn có id nguồn) và tra lại đúng việc cũ, nói rõ phần nào chỉ là suy ra.
- Chính cuộc trò chuyện thiết kế nmemo (transcript 2.1 MB): sau nén còn 34% (phần lớn còn lại là chữ
  ký mã hóa ngẫu nhiên, không nén được); cả file SQLite kèm chỉ mục 1.1 MB; `verify` khớp 100%.

## Windows

Đã chạy trên Windows 11 với Python 3.13 bản Microsoft Store, Claude Code 2.1.282 (dòng lệnh) và
2.1.309 (ứng dụng desktop): hook (qua Git Bash), MCP server, tự nhớ lại, nhập nền và tiêu hóa ngầm
đều chạy, không bật cửa sổ console nào. Phép thử dùng Claude Code thật nhưng trỏ vào một API giả
chạy ở máy, nên chưa có lượt tiêu hóa nào với model thật trên Windows.

- Cần lệnh `python3` trong PATH (bản Microsoft Store có sẵn). Không có thì sửa lệnh trong
  `hooks/hooks.json` và `.claude-plugin/plugin.json`.
- Cài thẳng từ thư mục repo, không cần GitHub: `claude plugin marketplace add D:\đường\dẫn\nmemo`
  rồi `claude plugin install nmemo@nmemo`. Hook và MCP chạy từ chính thư mục đó: sửa code thì phiên
  sau dùng luôn, đổi nhánh git là đổi bản đang chạy, đừng xóa hay dời thư mục.
- Quyền 600 không có tác dụng trên NTFS. `~/.nmemo` nằm trong thư mục người dùng nên mặc định chỉ
  bạn, SYSTEM và Administrators đọc được.
- `export` trong PowerShell 5.1: dấu `>` đổi mã hóa, file ra không còn đúng từng byte. Dùng
  `cmd /c "python3 bin\nmemo export s3 > s3.jsonl"` hoặc Git Bash.
- Windows từ chối đường dẫn từ 260 ký tự khi chưa bật `LongPathsEnabled`. nmemo tự thêm tiền tố
  `\\?\` khi đọc transcript và chụp file, nên phiên ở thư mục rất sâu vẫn được lưu (riêng `import`
  vẫn bỏ qua thư mục sâu quá giới hạn).

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
| `NMEMO_RECALL=0` | Tắt tự nhớ lại theo từng tin nhắn |
| `NMEMO_RECALL_CHARS` | Giới hạn phần tự nhớ lại mỗi tin nhắn (mặc định 1500 ký tự) |
| `NMEMO_DIGEST=0` | Tắt tiêu hóa sau phiên (không gọi model nào) |
| `NMEMO_DIGEST_MODEL` | Model cho việc tiêu hóa (mặc định `haiku`) |
| `NMEMO_PROFILE_CHARS` | Giới hạn hồ sơ đưa vào đầu phiên (mặc định 2500 ký tự) |
| `NMEMO_CLAUDE` | Đường dẫn lệnh `claude` nếu không nằm trong PATH |
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
| `nmemo/hooks.py`, `nmemo/mcp.py`, `nmemo/cli.py` | Hook (kể cả tự nhớ lại), MCP server (stdio), dòng lệnh |
| `nmemo/digest.py` | Tiêu hóa sau phiên: tóm tắt, sự thật, hồ sơ |
| `bench/compress_bench.py` | Đo nén trên dữ liệu của bạn |
| `docs/kien-truc.md` | Thiết kế đầy đủ |

## Chưa gom được (bước tiếp)

- Chat với AI ở nơi khác: app/web Claude, ChatGPT, Gemini. Các dịch vụ này không cho gắn hook, nên
  phải nhập từ file xuất dữ liệu (export) của từng nơi.
- Chat với người: Messenger, Telegram, Zalo, email.
- File trong máy mà AI không đụng tới: tài liệu, sách PDF, ảnh, video, ghi âm. Cần thêm bộ theo dõi thư mục.
- File mà lệnh shell tạo ra ngoài thư mục làm việc (ví dụ `/tmp`). File AI ghi bằng Write/Edit thì nằm
  ở đâu cũng được lưu.
- Tìm theo ý nghĩa (vector): tự nhớ lại hiện dựa trên từ ngữ trùng nhau, nên câu hỏi diễn đạt rất khác
  với bản ghi có thể bị bỏ sót (AI vẫn tự `search` thêm được).
- Tóm tắt theo tuần/tháng/năm cho lịch sử dài.

Không công cụ nào lưu được **phần suy nghĩ ẩn** của AI: nhà cung cấp chỉ gửi ra bản tóm tắt hoặc để
trống. nmemo lưu mọi thứ AI gửi ra: câu trả lời, dòng tiến độ, lệnh, kết quả, file.
