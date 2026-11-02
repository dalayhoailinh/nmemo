# nmemo — Thiết kế: máy bạn là bộ nhớ, AI cloud là bộ não

Tài liệu này trả lời một câu hỏi: lưu dữ liệu của bạn (sách, hàng trăm nghìn tin nhắn…) thế nào
để **gọn nhất**, **không mất một chữ**, và để AI trên cloud (Claude, Gemini…) **suy luận tốt nhất**
với **ít token nhất**.

```
┌──────────── MÁY CỦA BẠN (bộ nhớ) ─────────────┐           ┌─── CLOUD (bộ não) ───┐
│ raw/      bản gốc bất biến, nén zstd          │           │ Claude / Gemini / …  │
│ index.db  vị trí · full-text · episode ·      │◄─ công ──►│ suy luận, tự quyết   │
│           sự thật · tóm tắt · hồ sơ           │    cụ     │ cần đọc gì tiếp      │
│ nmemo MCP server: search · read · zoom ·      │  (MCP)    │                      │
│                   entity · remember · correct │           │                      │
└───────────────────────────────────────────────┘           └──────────────────────┘
     ▲ dữ liệu mới (chat, sách…)        chỉ những đoạn AI yêu cầu mới rời khỏi máy
```

## 0. Tóm tắt trong 1 phút

1. **Có hai kiểu "nén" khác hẳn nhau.** Nén *byte* để lưu trên ổ (zstd, xz…) và "nén" *token* để gửi
   cho AI. AI không đọc được dữ liệu đã nén byte, nên hai việc này phải tách riêng.
2. **Ổ 512 GB không phải nút thắt.** Sách 1000 trang chỉ ~2–2.5 MB chữ, nén còn ~0.7 MB → ổ 512 GB
   chứa ~700 nghìn cuốn. 100 nghìn tin nhắn chỉ vài MB. Thứ đắt thật sự là **token gửi lên cloud**.
3. **Không gửi hết — gửi đúng.** Bản gốc nằm nguyên vẹn ở máy. Gửi cho AI một **bản đồ nhỏ** (hồ sơ,
   mục lục, tóm tắt) và **công cụ để AI tự lấy đúng đoạn cần** (tìm kiếm, đọc theo ID).
   "Không bỏ sót" nghĩa là *cái gì cũng lấy được*, không phải *lần nào cũng gửi hết*.
4. **Làm nặng lúc ghi, để lúc hỏi thật rẻ.** Khi dữ liệu vào: đánh chỉ mục, tóm tắt nhiều tầng, rút ra
   sự thật có thời gian và nguồn. Khi hỏi: ~10 nghìn token/lượt thay vì hàng triệu.
5. **Cần đúng từng chữ thì không để AI "nhớ".** "Đọc lại trang 678" → công cụ trả nguyên văn từ ổ đĩa,
   kèm mã băm SHA-256; AI không chép lại nên không thể sai chữ.

## 1. Hai kiểu "nén" — đừng nhầm

### 1.1 Nén byte để lưu trữ (đã đo thật)

Đo bằng [`bench/compress_bench.py`](../bench/compress_bench.py) — bạn chạy lại được trên dữ liệu của mình.

**Sách thật**: *Opticks* (Isaac Newton), 567 KB tiếng Anh:

| Thuật toán | Còn lại | Tỉ lệ | Giải nén cả cuốn |
|---|---:|---:|---:|
| gzip -9 | 173.5 KB | 30.6% | 2.7 ms |
| xz -9e | 143.5 KB | 25.3% | 4.9 ms |
| zstd -19 | 146.6 KB | 25.8% | 0.6 ms |
| brotli -11 | 141.7 KB | 25.0% | 1.8 ms |
| PPMd bậc 8 | 117.5 KB | 20.7% | 75.5 ms |

**Tiếng Việt** (~105 KB: vimtutor bản tiếng Việt + chuỗi giao diện đã dịch): zstd -19 còn 25.9%,
PPMd còn 21.0% — gần như tiếng Anh.

**Đọc một trang bất kỳ** (cần cho "đọc lại trang 678"), sách trên chia thành 284 trang, đọc ngẫu nhiên
2000 lần, lần nào cũng khớp SHA-256 với bản gốc:

| Cách lưu | Dung lượng | Đọc 1 trang |
|---|---:|---:|
| Mỗi trang nén riêng | 45.5% | ~5 µs |
| Mỗi trang + từ điển zstd | 37.9% | ~5 µs |
| **Gộp 16 trang/khung** (đề xuất) | **32.2%** | ~30–40 µs |

**Tin nhắn ngắn** (~44 byte/tin): học từ 784 tin cũ, rồi nén lần lượt 784 tin "mới đến":

| Cách lưu | Tỉ lệ |
|---|---:|
| Nén từng tin riêng | 101.9% (còn to hơn!) |
| Từng tin + từ điển 3.7 KB | 77.6% |
| **Gộp khung 64 KB** (đề xuất) | **29.7%** |

Rút ra:

- Văn bản nén còn **~25–30%** với công cụ thông dụng, ~21% với PPMd. Các bộ nén "kỷ lục thế giới" dùng
  mạng nơ-ron (cmix, nncp) đưa văn bản tiếng Anh xuống ~11%, nhưng chậm hơn zstd hàng chục nghìn lần
  trở lên, giải nén cũng chậm như nén → không hợp với kho cần đọc ngẫu nhiên tức thì.
- Với 512 GB, chênh lệch giữa 30% và 11% của vài trăm MB chữ chỉ là vài chục MB. **Không đáng.**
  Chọn **zstd theo khung** (nhanh, đọc ngẫu nhiên được, thư viện có sẵn ở mọi ngôn ngữ).
- **Tin nhắn ngắn phải gộp khung**; nén từng tin riêng gần như vô ích.
- Cùng một nội dung, tiếng Việt UTF-8 tốn **hơn 52% byte** so với tiếng Anh (chữ có dấu chiếm 2–3 byte),
  nhưng sau nén chỉ còn hơn **10–17%** — phần dấu bị nén gần hết.
- **Chỉ mục còn to hơn dữ liệu:** chỉ mục full-text SQLite FTS5 = 48–67% kích thước gốc (≈ 2 lần bản
  nén). Vector embedding 1024 chiều float32 cho một cuốn sách (2000–4000 đoạn) ≈ 8–16 MB, gấp hơn 10 lần
  bản chữ đã nén; lượng tử hóa int8 còn 1/4, nhị phân còn 1/32.

### 1.2 "Nén" token để gửi cho AI

- AI cloud đọc **token** (mẩu chữ), tính tiền theo token, và mỗi lần gọi có giới hạn **context**
  (~1 triệu token với các model lớn hiện nay). 1 trang ≈ 500–1000 token (tiếng Việt tốn hơn tiếng Anh;
  đo chính xác bằng API đếm token của nhà cung cấp, ví dụ `count_tokens` của Anthropic)
  → 1000 trang ≈ 0.5–1 triệu token; 100 nghìn tin nhắn kèm người gửi/thời gian ≈ 2–4 triệu token.
- **Không gửi dữ liệu đã nén byte cho AI.** AI không giải nén được trong đầu; đổi sang base64 còn phình
  thêm 33% thành chuỗi ký tự vô nghĩa, tốn token hơn cả văn bản gốc.
- **Không tự chế viết tắt hay bỏ dấu để tiết kiệm.** Số token thường không giảm (chuỗi lạ bị cắt vụn),
  AI hiểu kém đi, và mất thông tin thật: bỏ dấu thì "ma, má, mà, mả, mã, mạ" thành một
  (đã thử: tìm "ma" khớp cả sáu).
- Cách duy nhất giảm token **mà không mất gì**: **gửi ít, nhưng cái gì cũng lấy được khi cần.** Giống thư
  viện: không bê cả thư viện đến cho giáo sư, mà đưa mục lục và cử một thủ thư lấy đúng trang được hỏi.
  Máy bạn là thư viện + thủ thư; AI cloud là giáo sư.

## 2. Nguyên tắc thiết kế

1. **Bản gốc là sự thật, bất biến, chỉ thêm (append-only).** Không sửa, không xóa, không thay bằng tóm tắt.
2. **Mọi thứ khác là bộ đệm dựng lại được**: chỉ mục, tóm tắt, sự thật, hồ sơ. Có model tốt hơn thì chạy
   lại từ bản gốc.
3. **Mọi mẩu phái sinh đều trỏ về nguồn** (ID tin nhắn, ID trang) → AI trích dẫn được, máy kiểm chứng được.
4. **Làm nặng lúc ghi, nhẹ lúc đọc.** Gắn thẻ người/chủ đề/cảm xúc/thời gian ngay khi nạp, để câu
   "liệt kê tất cả lần…" thành một truy vấn cơ sở dữ liệu, không phải đọc lại hàng triệu token.
5. **AI tự đi tìm** qua công cụ, và luôn được báo "tìm thấy 37, đang xem 10" để biết mình chưa thấy gì.
6. **Tách bạch ba loại**: điều bạn nói / sự thật đã xác nhận / suy luận của AI (kèm độ tin cậy và bằng chứng).
7. **Không phụ thuộc nhà cung cấp**: định dạng mở (JSONL, SQLite, Markdown) + giao thức mở (MCP) → đổi
   "bộ não" Claude ↔ Gemini lúc nào cũng được, bộ nhớ vẫn là của bạn.

### Dưới góc nhìn của AI: dữ liệu thế nào thì dễ suy luận nhất

- **Văn bản tự nhiên, đúng ngôn ngữ gốc, có cấu trúc** (tiêu đề, ID) — không mã hóa, không viết tắt lạ.
- **Bản đồ trước, chi tiết sau**: biết có gì và nằm đâu thì AI biết mình *chưa* biết gì và đi lấy thêm.
- **Địa chỉ ngắn, ổn định, trích dẫn được**: `p678#3`, `m88231`.
- **Nhiều mức phóng to/thu nhỏ**: năm → tháng → ngày → tin nhắn; cuốn → chương → trang.
- **Thời gian và nguồn trên mọi khẳng định** để suy luận về thay đổi và độ tin cậy.
- **Ngữ cảnh nhỏ mà đúng hơn ngữ cảnh khổng lồ**: AI suy luận sắc hơn trên 10 nghìn token liên quan
  so với 700 nghìn token phần lớn không liên quan, dù đọc được cả hai.

## 3. Kim tự tháp dữ liệu

```
              ┌────────────────────────────────┐
  Tầng 3      │ Bản đồ + Hồ sơ người dùng      │  1–5k token   luôn gửi kèm (được cache)
              ├────────────────────────────────┤
  Tầng 2      │ Tóm tắt chương / tháng / năm   │  ~1% gốc      gửi khi cần
              ├────────────────────────────────┤
  Tầng 1      │ Tóm tắt trang / episode,       │  ~10% gốc     AI tìm kiếm & chọn
              │ sự thật, thực thể, dòng t.gian │
              ├────────────────────────────────┤
  Tầng 0      │ BẢN GỐC (không mất gì)         │  100%         đọc nguyên văn theo ID
              └────────────────────────────────┘
  Chỉ mục ngang: ID · thời gian · người · chủ đề · full-text (có dấu / không dấu) · vector (tùy chọn)
```

AI luôn bắt đầu từ trên (rẻ, bao quát) rồi **khoan xuống** đúng chỗ cần (ít mà đúng).

### 3.1 Với một cuốn sách

| Tầng | Nội dung | ID ví dụ |
|---|---|---|
| 0 | File gốc (PDF/EPUB) + chữ theo từng trang, từng đoạn | `sach:x:p678`, `sach:x:p678#3` |
| 1 | Tóm tắt mỗi trang; thực thể (nhân vật, địa danh, khái niệm) kèm các trang nhắc tới; dòng thời gian sự kiện | `sach:x:the:<tên>` |
| 2 | Tóm tắt từng chương | `sach:x:c12` |
| 3 | Bản đồ sách: mục lục + 1–2 câu mỗi chương + thực thể chính (2–5k token) | `sach:x` |

Mẹo *contextual retrieval*: trước khi đánh chỉ mục một đoạn, gắn kèm một dòng ngữ cảnh
("Sách X › Chương 12 › đang bàn về Y") để kết quả tìm kiếm tự giải thích được.

### 3.2 Với hàng trăm nghìn tin nhắn (để hiểu một con người)

| Loại trí nhớ | Là gì | Ví dụ |
|---|---|---|
| **Nhật ký gốc** | Mọi tin nhắn, nguyên văn, bất biến | `m88231` 2026-10-28 23:41 An: "mai nộp rồi mà chưa xong…" |
| **Episode** | Một đoạn hội thoại (cắt theo khoảng lặng/chủ đề): tóm tắt 2–4 câu, người liên quan, cảm xúc, quyết định, việc dang dở | `e1882` "Căng thẳng vì deadline; quyết định chia nhỏ việc" (m88231–m88270) |
| **Sự thật** | Một câu ngắn có loại, thời gian hiệu lực, độ tin cậy, bằng chứng | `f903` thích cà phê đen không đường · từ 2025-03 · tin 0.9 · (m4410, m9012) |
| **Hồ sơ** | Một trang Markdown 1.5–3k token: bạn là ai, người thân, mục tiêu, giá trị, sở thích, cách muốn AI nói chuyện, tình hình gần đây, việc dang dở | `profile.md` (lưu trong git → xem "AI hiểu mình" thay đổi ra sao) |
| **Suy ngẫm** | Mẫu hành vi, giả thuyết do AI rút ra — **luôn ghi rõ là suy luận**, có bằng chứng, bạn sửa được | "Hay làm khuya khi gần deadline (e1201, e1530, e1882) · tin 0.7" |
| **Cách làm việc** | Điều gì hiệu quả khi giúp bạn | "Thích ví dụ ngắn trước, giải thích sau" |
| **Nhật ký hành động của AI** | Mọi câu trả lời AI đưa cho bạn, mọi lệnh AI chạy với đầu vào đầy đủ, kết quả trả về, nội dung đầy đủ của từng phiên bản file AI tạo/sửa | `i3` tool_call Write `hello.py` + nội dung · `i6` kết quả `xin chào nmemo` |

**Hai phía của cuộc trò chuyện đều là trí nhớ.** Chỉ nhớ lời người dùng thì AI biết bạn *muốn* gì
nhưng không biết mình *đã làm* gì: đã khuyên gì, đã viết file nào, lệnh nào từng lỗi. Vì vậy câu trả
lời, lệnh, kết quả và file của AI được lưu nguyên văn ngang hàng với tin nhắn của bạn (xem §5.4).

**Sự thật có hai trục thời gian**: *khi nào đúng ngoài đời* (`valid_from`, `valid_to`) và *khi nào hệ
thống biết* (`learned_at`). Không xóa sự thật cũ, chỉ đánh dấu hết hiệu lực: "sống ở Hà Nội (đến 2024)
→ sống ở TP.HCM (từ 2024)". Chính sự thay đổi là thông tin quý để hiểu một người.

## 4. Lưu trữ ở máy

```
data/
  raw/                              BẤT BIẾN — chỉ thêm; sao lưu từng file
    chat/2026/2026-10.jsonl.zst     chuỗi khung zstd độc lập, mỗi khung ~64 KB
    books/<id>/original.pdf         file gốc
    books/<id>/pages.jsonl.zst
  inbox/                            tin mới chưa đóng khung (JSONL thường, an toàn khi mất điện)
  index.db                          SQLite: vị trí khung, FTS5, episode, sự thật, tóm tắt… (dựng lại được)
  profile/profile.md                hồ sơ (git)
```

- **Đóng khung**: tin mới ghi vào `inbox/`; đủ ~64 KB hoặc hết ngày thì nén thành một khung zstd, ghi
  `(file, offset)` của từng tin vào `index.db`. Đọc một tin = giải nén một khung ≈ vài chục µs.
- **Bảng chính** (phác thảo):

```sql
-- bản gốc nằm ở đâu
CREATE TABLE item (
  id      TEXT PRIMARY KEY,            -- 'm88231', 'sach:x:p678'
  kind    TEXT,                        -- message | page | ...
  thread  TEXT, author TEXT, ts INTEGER,
  file    TEXT, frame_off INTEGER, frame_len INTEGER,  -- khung zstd nào trong file
  off     INTEGER, len INTEGER,        -- vị trí bên trong khung sau khi giải nén
  sha256  BLOB
);

-- tìm kiếm: cột nguyên dấu + cột đã "gập" (chữ thường, bỏ dấu, đ→d)
CREATE VIRTUAL TABLE item_fts USING fts5(body, folded, content='',
  tokenize='unicode61 remove_diacritics 0');

CREATE TABLE episode (
  id TEXT PRIMARY KEY, thread TEXT, ts_start INTEGER, ts_end INTEGER,
  first_item TEXT, last_item TEXT,
  summary TEXT, people TEXT, topics TEXT, mood TEXT, importance INTEGER
);

CREATE TABLE fact (
  id TEXT PRIMARY KEY, statement TEXT,
  kind TEXT,                           -- fact|preference|relationship|goal|event|habit|inference
  subject TEXT, valid_from TEXT, valid_to TEXT, learned_at TEXT,
  confidence REAL, importance INTEGER,
  status TEXT,                         -- active|superseded|retracted|disputed
  supersedes TEXT, evidence TEXT       -- danh sách ID nguồn
);

CREATE TABLE summary (
  id TEXT PRIMARY KEY,
  level TEXT,                          -- page|chapter|book|day|week|month|year
  scope TEXT, ts_start INTEGER, ts_end INTEGER, text TEXT, children TEXT
);
```

- **Tiếng Việt — bốn cái bẫy:**
  1. SQLite FTS5 `remove_diacritics` gập được hầu hết dấu ("khong" ra "Không", "pho" ra "phở",
     "nguyen van linh" ra "Nguyễn Văn Linh") **nhưng không gập "đ"**: "duong" không ra "đường"
     (đã thử trên SQLite 3.53). → Tự tạo cột `folded` với `đ→d`, và gập câu truy vấn theo đúng cách đó.
  2. Chuẩn hóa Unicode NFC cho chỉ mục ("ệ" có thể là 1 hoặc 3 mã điểm); bản gốc vẫn giữ nguyên byte.
  3. Tài liệu cũ dùng bảng mã TCVN3/VNI → chuyển sang Unicode trước khi nạp.
  4. Tìm không dấu chỉ là phương án dự phòng; kết quả khớp nguyên dấu được xếp trên.
- **Vector embedding (làm sau cũng được)**: model đa ngôn ngữ chạy ở máy (ví dụ BGE-M3, multilingual-e5)
  qua `sqlite-vec` hoặc LanceDB, lượng tử hóa cho nhỏ. Có thể chưa cần: AI cloud tự nghĩ ra nhiều cách
  gõ truy vấn (đồng nghĩa, có/không dấu) trên full-text đã rất hiệu quả — Claude Code cũng tìm code bằng
  grep chứ không bằng vector. Bỏ vector thì máy gần như chỉ còn việc lưu và tra cứu.
- **Ảnh, âm thanh mới là thứ ăn ổ cứng.** Giữ file gốc (JPEG chuyển sang JPEG XL được **không mất dữ
  liệu**, nhỏ hơn ~20%, khôi phục đúng từng bit). Tạo **bản mô tả bằng chữ** (chú thích ảnh, OCR, chép
  lời voice) làm dữ liệu phái sinh để AI "thấy" ảnh qua chữ, khỏi gửi ảnh mỗi lần.

## 5. Luồng ghi: dữ liệu vào

### 5.1 Nạp lần đầu (một cuốn sách, 100 nghìn tin nhắn cũ)

1. **Nhập & chuẩn hóa** (máy): đọc file xuất từ Messenger/Zalo/Telegram… về một định dạng chung
   `{id, nguồn, thread, ts, người gửi, nội dung, trả lời cho, đính kèm}`; loại trùng bằng hash; ghi vào `raw/`.
2. **Đánh chỉ mục** (máy, vài giây đến vài phút): FTS5 + metadata.
3. **Cắt episode** (máy, quy tắc đơn giản): im lặng quá ~45 phút hoặc đổi chủ đề rõ thì mở episode mới.
4. **Tiêu hóa** (cloud, Batch API, model rẻ): mỗi episode / mỗi trang → tóm tắt, thẻ (người, chủ đề,
   cảm xúc), sự thật ứng viên kèm ID bằng chứng, điểm quan trọng. Ép kết quả theo JSON schema
   (structured output) để luôn đúng khuôn.
5. **Đối chiếu sự thật** (cloud, **theo thứ tự thời gian**): mỗi sự thật ứng viên → máy tìm sự thật cũ
   tương tự → AI chọn THÊM / CẬP NHẬT / HẾT HIỆU LỰC / BỎ QUA. Chạy theo thời gian để cái mới đè cái cũ đúng.
6. **Cuộn tóm tắt** (cloud): ngày → tuần → tháng → năm; với sách: trang → chương → cuốn.
7. **Suy ngẫm & hồ sơ** (cloud, model mạnh, khối lượng nhỏ): viết `profile.md`, các giả thuyết kèm bằng
   chứng, danh sách "điều chưa rõ — nên hỏi khi thích hợp".

### 5.2 Mỗi tin nhắn mới (thời gian thực)

```
tin mới ──► ghi inbox/ + FTS                         (vài ms, 0 token)
        ──► máy tìm trí nhớ liên quan                (vài ms, 0 token)
        ──► lắp prompt: [hồ sơ] [hội thoại hiện tại] [trí nhớ liên quan] [tin mới]
        ──► AI cloud trả lời; thiếu thì tự gọi search / read / zoom
        ──► tin có thông tin bền vững ("mình vừa đổi việc") → AI gọi remember(...) ngay
        ──► câu trả lời của AI cũng vào nhật ký, để lần sau biết mình đã khuyên gì
```

Chọn trí nhớ nào để gửi (giống *Generative Agents*): điểm = **liên quan** + **gần đây** (giảm dần theo
thời gian) + **quan trọng** (chấm lúc tiêu hóa), có trọng số; luôn kèm các mục "ghim" (người thân,
việc dang dở). Ngân sách cố định, ví dụ tối đa ~4k token cho trí nhớ mỗi lượt.

### 5.3 Hợp nhất định kỳ (như "ngủ")

- **Cuối mỗi cuộc trò chuyện / mỗi đêm** (Batch API): tiêu hóa episode mới, đối chiếu sự thật, cập nhật
  "tình hình gần đây".
- **Mỗi tuần / tháng**: cuộn tóm tắt, suy ngẫm, viết lại hồ sơ, commit git để xem diff.
- **Khi có model tốt hơn**: dựng lại tầng 1–3 từ bản gốc.

### 5.4 Ghi cả việc AI đã làm (đã hiện thực trong nmemo 0.1 cho Claude Code)

Claude Code ghi mỗi phiên ra một transcript JSONL, gồm cả những gì AI làm. nmemo gắn 7 hook để chép
toàn bộ vào kho trước khi Claude Code tự xóa transcript cũ (mặc định sau 30 ngày):

| Hook | nmemo làm gì |
|---|---|
| `SessionStart` | Nạp phần đã có; lần đầu thì nhập nền mọi transcript cũ; chèn vài dòng "các phiên trước ở thư mục này" vào ngữ cảnh |
| `UserPromptSubmit` | **Tự nhớ lại**: tìm trong kho theo tin nhắn mới, đưa cho AI tối đa 5 dòng liên quan từ các phiên trước (ưu tiên tóm tắt và sự thật, bỏ từ quá phổ biến, không lặp lại trong một phiên); ghi giờ bắt đầu lượt |
| `PostToolUse` (Write, Edit, MultiEdit, NotebookEdit) | Chụp nội dung đầy đủ của file ngay sau khi AI sửa |
| `Stop` | Nạp các dòng mới của lượt (câu hỏi, câu trả lời, lệnh, kết quả); chụp thêm file mà lệnh shell đã đổi trong lượt |
| `SubagentStop` | Nạp transcript riêng của subagent, gắn vào phiên cha |
| `PreCompact`, `SessionEnd` | Nạp nốt, nén các dòng còn chờ thành khung; `SessionEnd` còn khởi động **tiêu hóa ngầm**: model rẻ (qua `claude -p`, không công cụ, không lưu phiên) viết tóm tắt phiên + sự thật về người dùng, rồi cập nhật `profile.md`, file được đưa vào đầu mỗi phiên sau |

Mỗi dòng transcript lưu đúng từng byte (SHA-256, xuất lại được y hệt file gốc); các mục tìm kiếm chỉ
là con trỏ vào dòng gốc, nên đọc ra luôn là nguyên văn. Đã chạy thử với Claude Code thật: ở một phiên
mới, AI tự dùng `sessions` → `search` → `read` để nói lại đúng file nó đã tạo ở phiên trước, nội dung
và kết quả chạy. Chi tiết cài đặt và giới hạn: [README](../README.md).

## 6. Luồng đọc: mỗi lần hỏi

### 6.1 Bố cục prompt để tận dụng cache

```
[định nghĩa công cụ]                      cố định         ─┐
[chỉ dẫn hệ thống: cách dùng trí nhớ]     cố định          ├─ cache: đọc lại rẻ hơn ~10 lần trở lên
[hồ sơ + bản đồ dữ liệu]                  đổi theo ngày   ─┘
[hội thoại hiện tại]                      chỉ nối thêm    ── cache tăng dần theo lượt
[trí nhớ liên quan lượt này + tin mới]    đổi mỗi lượt
```

Phần cố định đặt trước, phần hay đổi đặt sau. Không nhét giờ hiện tại hay dữ liệu thay đổi vào phần đầu,
nếu không cache mất tác dụng.

### 6.2 Công cụ máy bạn cung cấp cho AI (nmemo MCP server)

| Công cụ | Việc | Ghi chú |
|---|---|---|
| `search(query, từ, đến, người, loại, limit, cursor)` | Tìm trong mọi tầng | Trả dòng ngắn + `tổng_số_khớp` + `cursor` để xem tiếp |
| `read(id, trước, sau)` | Nguyên văn theo ID (trang, tin, đoạn) | Kèm SHA-256; người dùng thấy thẳng kết quả công cụ |
| `zoom(id)` | Tóm tắt của một nút + danh sách nút con | Cuốn → chương → trang; năm → tháng → ngày |
| `entity(tên)` | Thẻ người/nơi/khái niệm: quan hệ, sự thật, dòng thời gian | |
| `remember(câu, loại, bằng_chứng)` | AI ghi sự thật mới | Nhãn "do AI ghi", bạn duyệt được |
| `correct(id, …)` | Sửa hoặc rút lại sự thật | Lời bạn luôn thắng suy luận của AI |

Claude Desktop, Claude Code và Gemini CLI kết nối được MCP server chạy ở máy. Nếu tự viết ứng dụng gọi API,
dùng function calling với cùng định nghĩa công cụ.

### 6.3 Ba ví dụ

- **"Đọc lại nguyên văn trang 678."** AI gọi `read("sach:x:p678")` → máy giải nén một khung (~40 µs),
  kiểm SHA-256, trả nguyên văn. Thử ngay: `python bench/compress_bench.py sach.txt --page 678`.
  Tốn khoảng 1k token.
- **"Vì sao nhân vật A phản bội B?"** Bản đồ sách đã có sẵn → `entity("A")` → thấy các trang then chốt →
  `zoom` chương 12, 15 → `read` 3 trang → suy luận, trích dẫn `p402#2`, `p517#1`. Tốn ~8–15k token
  thay vì 750k.
- **Tin nhắn mới: "mai thuyết trình rồi, run quá".** Máy tự tìm các episode thuyết trình trước, suy
  ngẫm "hay lo trước khi thuyết trình; tập dượt trước 2 hôm thì ổn (e301, e977)", việc dang dở liên
  quan → AI trả lời như người đã biết bạn nhiều năm. Tốn ~6–10k token.

### 6.4 Định dạng gửi cho AI: gọn mà rõ

Dòng ngắn, ID ngắn, ngày gọn. Không dùng JSON dài dòng (ngoặc, tên khóa lặp lại đều tốn token):

```
<tri_nho truy_van="thuyết trình" tong_khop="23" dang_hien="3">
[e977 2026-05-12 · học tập · quan trọng 7] Thuyết trình môn X; tập trước 2 hôm, kết quả tốt (m40112–m40180)
[f640 thói quen · suy luận · tin 0.7] Hay lo lắng trước khi thuyết trình (e301, e977)
[f201 sở thích · từ 2025-03] Thích được động viên ngắn gọn, không sáo rỗng (m9012)
</tri_nho>
```

## 7. Ngân sách token và chi phí

Giả định: 1000 trang ≈ 750k token; 100 nghìn tin nhắn ≈ 3 triệu token. Giá Claude API tháng 10/2026
(USD / 1 triệu token, model hiện hành của từng dòng): dòng Opus $4 vào / $20 ra (đọc cache $0.20);
dòng Sonnet $2 / $10 (đọc cache $0.20); dòng Haiku $0.10 / $0.50 (prompt ≤ 100k token). Batch API giảm 50%. Ghi cache tốn
1.25× giá thường (giữ 5 phút) hoặc 2× (giữ 1 giờ). Giá thay đổi theo thời gian — kiểm tra trang giá
chính thức; Gemini và các nhà cung cấp khác có cơ chế tương tự (context caching, batch) với giá khác.

| Tình huống | Token vào mỗi lần | Chi phí ước tính |
|---|---:|---:|
| Gửi nguyên cuốn sách mỗi câu hỏi (Opus, không cache) | 750k | ~$3 / câu |
| … có cache, các câu liên tiếp trong lúc cache còn sống | 750k (đọc cache) | ~$0.15 / câu (+ ~$3.75 lần đầu) |
| Gửi nguyên 100 nghìn tin nhắn | 3M | **Không thể** — vượt giới hạn 1M token |
| **Cách đề xuất** (Opus) | ~10k | **~$0.05 / lượt** |
| **Cách đề xuất** (Sonnet) | ~10k | **~$0.025 / lượt** |
| Tiêu hóa một lần 100 nghìn tin nhắn (Batch) | ~4.5M tổng | Haiku ~$0.5 · Sonnet ~$10 · Opus ~$20 |
| Tiêu hóa một lần một cuốn sách (Batch) | ~1M tổng | Haiku ~$0.1 · Sonnet ~$2 · Opus ~$3.5 |

Các con số chưa tính token "suy nghĩ" của model; đặt mức effort thấp cho việc trích xuất hàng loạt.
Riêng một cuốn sách vừa context thì gửi nguyên cuốn + cache cũng chạy được trong một phiên ngắn, nhưng
không mở rộng được cho cả thư viện hay cả đời tin nhắn.

Mẹo tiết kiệm, xếp theo hiệu quả:

1. Truy xuất thay vì gửi hết.
2. Tóm tắt nhiều tầng, chỉ khoan xuống khi cần.
3. Prompt caching cho phần cố định.
4. Batch API cho mọi việc không gấp.
5. Model rẻ cho tiêu hóa hàng loạt; model mạnh cho suy ngẫm và trò chuyện.
6. Không gửi lại đoạn AI đã đọc trong cùng phiên.
7. Định dạng dòng gọn thay vì JSON.

## 8. "Không bỏ sót" và "đúng từng chữ"

**Đúng từng chữ**

- Nguyên văn luôn lấy từ ổ đĩa qua `read`; SHA-256 được kiểm khi nạp và khi đọc.
- Khi AI trích dẫn, máy so khớp chuỗi với nguồn → đánh dấu trích dẫn sai.
- Định kỳ kiểm tra toàn vẹn: giải nén tất cả, so hash.

**Không bỏ sót**

- Cái gì cũng có ID và lấy được → không có dữ liệu "mất tích".
- Kết quả tìm kiếm luôn báo tổng số khớp và cho phân trang → AI biết còn gì chưa xem.
- Câu "liệt kê **tất cả** lần…" không dựa vào top-k: dùng thẻ đã gắn sẵn (người, chủ đề, cảm xúc) để
  truy vấn đầy đủ; nếu buộc phải đọc hết, chạy map-reduce bằng model rẻ trên toàn bộ phần khớp.
- Có bộ câu hỏi kiểm thử với đáp án biết trước ("Mẹ mình sinh ngày nào?", "Trang 678 câu đầu là gì?"),
  chạy lại mỗi lần đổi thiết kế → đo được độ sót và chi phí thay vì đoán.

## 9. Riêng tư và an toàn dữ liệu

- Truy xuất vốn là **tối thiểu hóa dữ liệu**: mỗi lượt, cloud chỉ thấy vài đoạn liên quan, không thấy
  cả cuộc đời bạn.
- Gắn thẻ nhạy cảm (sức khỏe, tài chính, mật khẩu, số giấy tờ) → không gửi khi chưa cho phép. Có thể
  thay tên người bằng bí danh nhất quán (`[P1]`) trước khi gửi rồi đổi lại ở máy.
- Đọc chính sách dữ liệu của nhà cung cấp; điều khoản cho API và cho ứng dụng chat thường khác nhau.
- Mã hóa ổ đĩa hoặc cơ sở dữ liệu ở máy.
- **Sao lưu là bắt buộc**, vì máy bạn là bản duy nhất. Quy tắc 3-2-1: 3 bản, 2 loại thiết bị, 1 bản ở
  nơi khác. Bản lên cloud phải **mã hóa trước** (restic, borg, age) để nhà cung cấp chỉ thấy dữ liệu đã
  mã hóa. Thư mục `raw/` bất biến nên sao lưu tăng dần rất nhẹ.

## 10. Những điều KHÔNG nên làm

- Gửi dữ liệu đã nén hoặc base64 cho AI.
- Bỏ dấu, tự chế viết tắt để "nén" cho AI.
- Để AI chép lại đoạn nguyên văn dài từ trí nhớ của nó.
- Thay bản gốc bằng tóm tắt, hoặc sửa/xóa bản gốc.
- Trộn suy luận của AI với điều bạn thật sự nói.
- Fine-tune model để nó "nhớ" dữ liệu của bạn: đắt, không cập nhật từng tin được, không trích nguồn được,
  vẫn có thể bịa, và phải tải toàn bộ dữ liệu lên.
- Dùng tính năng "memory" của nhà cung cấp làm nguồn sự thật: không minh bạch, không kiểm chứng được,
  không mang sang AI khác được.
- Tối ưu nén byte quá mức (cmix, nén bằng mạng nơ-ron): tiết kiệm vài chục MB, đổi lại chậm hàng nghìn lần.

## 11. Lộ trình

| Bước | Làm gì | Dùng được gì |
|---|---|---|
| 1 | Nhập 1 nguồn chat + 1 cuốn sách → `raw/` + SQLite FTS5; MCP server với `search`, `read`; nối Claude Desktop hoặc Claude Code; 20 câu hỏi kiểm thử đầu tiên. **Đã làm cho nguồn đầu tiên: phiên Claude Code (nmemo 0.1).** Còn: chat từ nơi khác, sách | Tìm lại tin cũ; "đọc trang 678" chính xác |
| 2 | Cắt episode, tiêu hóa bằng Batch API; bảng `fact`; `profile.md` đầu tiên. **Đã làm bản đầu cho phiên Claude Code: tóm tắt mỗi phiên, sự thật, hồ sơ, tự nhớ lại theo từng tin nhắn (nmemo 0.1).** | AI trả lời có ngữ cảnh về bạn |
| 3 | Luồng thời gian thực: `inbox/`, đóng khung, `remember`, tiêu hóa cuối phiên / mỗi đêm | Bộ nhớ tự lớn lên mỗi ngày |
| 4 | Cuộn tóm tắt ngày → năm, thẻ thực thể, dòng thời gian, suy ngẫm có bằng chứng; giao diện duyệt/sửa sự thật | "Năm ngoái mình thế nào?"; AI hiểu mẫu hành vi |
| 5 | Mở rộng bộ kiểm thử; đo độ sót và chi phí; tinh chỉnh ngân sách token | Biết chắc hệ thống tốt đến đâu |
| 6 | (Tùy chọn) vector embedding, bí danh hóa, sao lưu mã hóa tự động, ảnh/voice | Tìm theo ý nghĩa; riêng tư hơn |

## 12. Tham khảo

- MemGPT / Letta — bộ nhớ nhiều tầng kiểu hệ điều hành cho LLM (Packer và cộng sự, 2023).
- Generative Agents — dòng trí nhớ; chọn theo liên quan, gần đây, quan trọng; suy ngẫm (Park và cộng sự, 2023).
- Mem0 — các thao tác THÊM / CẬP NHẬT / XÓA / BỎ QUA cho trí nhớ dài hạn (2025).
- Zep / Graphiti — đồ thị tri thức hai trục thời gian cho trí nhớ agent (2025).
- RAPTOR — cây tóm tắt đệ quy để truy xuất (Sarthi và cộng sự, 2024); GraphRAG (Microsoft, 2024).
- Contextual Retrieval (Anthropic, 2024) — gắn ngữ cảnh vào từng đoạn trước khi đánh chỉ mục.
- Model Context Protocol (MCP) — giao thức mở để AI dùng công cụ và dữ liệu bên ngoài.
- "Language Modeling Is Compression" (Delétang và cộng sự, 2023) — vì sao nén tốt nhất chính là dự đoán
  tốt nhất, và vì sao cách đó không thực dụng cho kho cần đọc ngẫu nhiên.
