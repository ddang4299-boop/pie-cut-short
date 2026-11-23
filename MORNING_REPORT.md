# Báo cáo buổi sáng — ShortForge Local

Mỗi phase thêm một mục bên dưới. Các quyết định chi tiết nằm trong `DECISIONS.md`; việc cần người làm nằm trong `BLOCKED.md` (nếu có).

---

## Phase 0 — Khung dự án + i18n ✅

**Đã làm**
- Backend FastAPI (`backend/`): `GET /health` trả về phiên bản ffmpeg, thiết bị (máy này: **CUDA**, RTX 4060 Laptop) và bộ mã hoá ưu tiên (`h264_nvenc`).
- `pipeline/preflight.py`: kiểm tra luồng video + audio, codec đọc được, dung lượng trống ≥ 3× file nguồn, GPU, VFR, video dọc, video > 2 giờ. Trả về danh sách mã lỗi/cảnh báo kèm tham số. Chạy thử: `python -m pipeline.preflight <video>`.
- `app/errors.py`: sổ đăng ký mã lỗi. Backend chỉ trả `{code, severity, params}`, frontend tự dịch.
- Frontend (`frontend/`): React + Vite + TypeScript + Tailwind, giao diện tối đúng bảng màu trong spec, sidebar 64px với 5 trang, header có nút chuyển EN/VI, ngăn kéo "Jobs", trạng thái backend (xanh/đỏ) đã dịch.
- i18n: `en.json` / `vi.json`, ngôn ngữ mặc định lấy theo trình duyệt, lựa chọn được lưu lại. Phông Inter + JetBrains Mono có sẵn bộ chữ tiếng Việt, đóng gói cục bộ (không dùng CDN).
- `.env.example`, `.gitignore`, `requirements.txt`, `README.md`; `scripts/make_sample.py` tạo video thử 3 phút.
- Sửa `run-overnight.ps1`: hiện tiến trình trực tiếp và hết báo nhầm "usage limit" (bản cũ tự khớp chữ trong log nên đứng chờ 30 phút vô ích).

**Cách thử**
```
cd backend && ..\.venv\Scripts\uvicorn app.main:app --reload
cd frontend && npm run dev          # mở http://localhost:5173
```
Header hiện "Backend online" màu xanh; bấm EN/VI thì toàn bộ chữ trên màn hình đổi ngôn ngữ.

**Kiểm thử**
- pytest backend: **44/44 pass** (preflight, mã lỗi, /health, các khoá locale khớp nhau).
- Vitest frontend: **11/11 pass**; `npm run build` pass.
- Smoke test trên trình duyệt (`npm run smoke`, dùng Edge): **pass**. Đã kiểm tra trạng thái xanh, đủ 5 trang + Jobs ở cả EN và VI, không lọt chữ của ngôn ngữ kia, lựa chọn được lưu, trạng thái lỗi khi backend tắt, không tràn ngang ở 1280px.
- Preflight trên `samples/synthetic_3min.mp4`: OK, khoảng 3 giây.

**Quyết định quan trọng**
- Máy chưa có Python 3.11 nên venv dùng **Python 3.14** (code vẫn tương thích 3.11). Cách đổi có trong `DECISIONS.md`.
- Frontend gọi backend qua proxy `/api/*` của Vite.

**Việc bạn nên làm khi thức dậy**
- Thả **một video thật 2–5 phút có giọng nói** (tiếng Việt hoặc tiếng Anh) vào `samples/`. Hiện chỉ có video tổng hợp không có tiếng nói, nên phần nhận dạng giọng nói chưa được thử với dữ liệu thật.
- (Tuỳ chọn) Cài Python 3.11/3.12 nếu muốn đúng phiên bản trong spec.

---

## Phase 0b — Tái sử dụng claude-shorts ⏭️

Bỏ qua: không có thư mục `vendor/claude-shorts`. Nếu muốn dùng lại repo đó, hãy clone vào `vendor/claude-shorts` rồi chạy lại Phase 0b.

---

## Phase 1 — Nhập video + Chép lời + Thư viện ✅

**Đã làm được**
- **Nhập video** theo 3 cách: kéo thả hoặc bấm "Chọn tệp" (Ctrl+O, tải lên có thanh tiến trình và nút huỷ); dán **đường link** (tải bằng yt-dlp, tối đa 1080p); dán **đường dẫn tệp trên máy** (dùng trực tiếp, không sao chép, không bao giờ bị xoá).
- Bước **kiểm tra (preflight)** chạy ngay sau khi nhập và hiện kết quả xanh / vàng / đỏ. Xử lý chỉ bắt đầu khi bấm **Bắt đầu**, kèm chọn ngôn ngữ nói (tự nhận diện / Việt / Anh / xen kẽ) và ghi chú cho AI.
- **Bản xem trước 720p**, tốc độ khung hình cố định (dùng NVENC, lỗi thì tự chuyển sang x264), có lưu cache.
- **Chép lời** bằng faster-whisper large-v3 trên GPU, có mốc thời gian từng từ và VAD, tự chuyển sang CPU nếu GPU lỗi. Lời thoại được cache theo (tệp + model + ngôn ngữ).
- **Hàng đợi tác vụ** lưu trong SQLite: tắt app giữa chừng thì mở lại sẽ chạy tiếp, tệp tạm bị dọn. Một tác vụ làm sập app 3 lần sẽ bị dừng hẳn (không lặp vô tận). Mỗi tác vụ có nhật ký riêng.
- **Thư viện**:
  - Mỗi thẻ video có ảnh thu nhỏ, thời lượng, độ phân giải, fps, dung lượng, ngôn ngữ và nhãn loại nội dung (bấm để đổi).
  - Thanh tiến trình 5 bước: Kiểm tra → Bản xem trước → Chép lời → Phân tích → Khoảnh khắc.
  - Có nút Huỷ, Thử lại, Xem nhật ký.
  - Menu: Mở Khoảnh khắc · Xem lời thoại · Chạy lại từ bước… (có hỏi xác nhận) · Xoá (hỏi có xoá luôn tệp media không).
- **Ngăn Tác vụ** (Jobs) hiện tiến trình, trạng thái, nhật ký và nút huỷ. Header hiện tên video nguồn đang chọn và số tác vụ đang chạy.
- **Trình xem lời thoại**: phát bản xem trước, tô sáng từ đang nói, bấm vào từ để nhảy tới đoạn đó.
- Toàn bộ chữ mới, kể cả mọi mã lỗi từ backend, đều có cả tiếng Anh và tiếng Việt.

**Kết quả chạy thật** (qua giao diện, bằng trình duyệt tự động)
- Tải `samples/speech_en_sample.mp4` lên (2 phút 17 giây, giọng đọc tiếng Anh): kiểm tra đạt, bấm Bắt đầu → **xong cả 3 bước trong 34 giây** (large-v3 trên RTX 4060, đã gồm thời gian nạp model), ra 338 từ. Chữ chép đúng nội dung ("Welcome back to the channel…").
- Chạy lại lần hai dùng cache nên chỉ mất 10 giây.

**Kiểm thử**
- pytest backend: **116/116 pass** (ingest, proxy, transcribe, hàng đợi + khôi phục, API nguồn, các khoá locale).
- Vitest: **23/23 pass**; `npm run build` pass.
- Smoke test (Edge): **pass**. Gồm cả luồng thật: tải lên → kiểm tra → Bắt đầu → chép lời → xem lời thoại → bấm từ để tua → xoá.
- Không tràn ngang ở 1280px, kể cả tiếng Việt khi đang có video chạy.

**Quyết định quan trọng** (chi tiết trong `DECISIONS.md`)
- Chép lời chạy ở **tiến trình con**: huỷ là dừng ngay, và lỗi GPU không làm sập backend.
- Phải tự giải mã âm thanh trước khi đưa vào faster-whisper, vì bản PyAV mới cho Python 3.14 không tương thích với faster-whisper.
- Model Whisper lưu trong `data/models`; thư viện CUDA lấy từ gói pip, không cần cài CUDA.
- Mốc thời gian `mm:ss.ff` dùng **phần trăm giây** (không phải số khung hình).

**Việc bạn nên làm khi thức dậy**
- Thả **một video thật có giọng nói tiếng Việt** vào `samples/` rồi nhập qua Thư viện. Máy không có giọng đọc tiếng Việt nên phần chép lời tiếng Việt **chưa được kiểm tra** với dữ liệu thật.
- Thử nhập bằng một đường link YouTube. Phần tải về đã có test giả lập nhưng chưa tải thật, vì quy tắc autopilot hạn chế gọi mạng.

---

## Phase 2 — Phân tích + Đề xuất khoảnh khắc + Duyệt khoảnh khắc ✅

**Đã làm**
- **Phân tích** (`pipeline/analyze.py`):
  - Độ to theo từng giây (ffmpeg ebur128) và 30 đỉnh âm thanh.
  - Khoảng lặng (silencedetect, ngưỡng tự điều chỉnh theo bản ghi).
  - Chuyển cảnh (PySceneDetect) và dạng sóng cho timeline.
- **Nhận diện loại nội dung** (`pipeline/content_type.py`): `talking_head | screen_recording | podcast | gameplay_facecam`.
  - Dựa trên khuôn mặt (MediaPipe, dự phòng bằng Haar của OpenCV), chuyển động và tỉ lệ có lời nói.
  - Người dùng bấm vào nhãn để đổi; lựa chọn đó luôn được ưu tiên.
- **Đề xuất khoảnh khắc** (`pipeline/moments.py`), 5 nhà cung cấp:
  - `claude_code` (mặc định): backend ghi `analysis_request.md` đúng định dạng A.1, bạn chạy `/find-moments <id>` trong Claude Code, app tự phát hiện `moments.json` và nạp vào.
  - `anthropic` / `openai` / `ollama`: đã có code nhưng **chưa gọi thật lần nào**, chỉ test giả lập.
  - `heuristic`: dùng ngoại tuyến, không cần AI.
  - Điểm tổng luôn được **tính lại** từ 5 tiêu chí theo trọng số trong Cài đặt.
  - Video dài được chia thành nhiều phần.
- **Bắt dính ranh giới** (`pipeline/snap.py`): ưu tiên đầu/cuối câu, rồi đến ranh giới từ, rồi tinh chỉnh theo khoảng lặng (±0,5 giây), thêm 0,15 giây đệm. **Không bao giờ cắt giữa một từ.**
- **Trang Duyệt khoảnh khắc** (3 cột):
  - **Danh sách bên trái:**
    - Lọc Tất cả / Đề xuất / Đã duyệt / Đã loại, sắp theo điểm hoặc thời gian.
    - Mỗi thẻ có: vòng điểm màu, 5 thanh tiêu chí (di chuột để xem số), lý do thu gọn 2 dòng, nút ✓ / ✕.
  - **Ở giữa:**
    - Trình phát có "Xem trước 9:16" và chế độ lặp, chỉ phát trong khoảnh khắc đang chọn.
    - Minimap toàn video.
    - Timeline phóng to ±10 giây: dạng sóng, khoảng lặng màu xám, chuyển cảnh, tay cầm vào/ra bắt dính theo từ (giữ Alt để tắt). Thả tay cầm xong sẽ hiện "Đã bắt dính vào …".
    - Ô thời gian gõ được hoặc kéo để tua; sửa được tiêu đề và câu mở đầu.
  - **Bên phải:** bản ghi lời thoại tô sáng từ đang nói và tô nền các câu thuộc khoảnh khắc. Bấm vào từ để tua; bôi đen một đoạn thì hiện nút **"Tạo khoảnh khắc"**, lấy luôn đoạn chữ đó làm tiêu đề.
  - **Thanh dưới:** kiểu phụ đề, nền tảng và nút **"Duyệt & mở Trình biên tập"**.
  - **Tự lưu** sau 500 ms (header hiện "Đang lưu… / Đã lưu"), **Hoàn tác / Làm lại** 100 bước.
  - Phím tắt Space, J/K/L, ←/→, Shift+←/→, I/O, A, R, ↑/↓, Ctrl+Z.
- **Thẻ trong Thư viện** khi đang chờ hiện "Đang chờ /find-moments" kèm các nút **Sao chép lệnh**, **Kiểm tra ngay** và **Tìm ngoại tuyến**.

**Kết quả chạy thật**
- Chạy video mẫu `speech_en_sample.mp4` qua cả pipeline (khoảng 23 giây khi đã có cache lời thoại).
  - Loại nội dung nhận ra là `screen_recording`. Đúng, vì video mẫu là ảnh thử màu, không có mặt người.
  - Phần phân tích ra 20 đỉnh âm thanh và 58 khoảng lặng.
- Tôi đóng vai `/find-moments` và viết `moments.json`: app nạp **8 đề xuất**.
- Smoke test trên trình duyệt Edge đạt **61/61**, gồm cả đủ tiêu chí "Hoàn thành khi":
  - Chỉnh điểm vào/ra bằng cách gõ và bằng kéo tay cầm.
  - Duyệt 2 khoảnh khắc.
  - Tạo 1 khoảnh khắc thủ công từ đoạn bôi đen.
  - Loại một khoảnh khắc rồi Ctrl+Z để hoàn tác.
  - Tải lại trang thì mọi thứ còn nguyên.
  - Kiểm tra thêm: điểm cắt không rơi vào giữa từ, tiếng Việt không bị tràn ngang.

**Lỗi tìm thấy và đã sửa trong lúc test thật**
- Bấm vào phần "lý do" của một thẻ chỉ mở rộng đoạn chữ mà không chọn thẻ, nên phím A/R tác động nhầm khoảnh khắc khác. Đã sửa: bấm ở đâu trên thẻ cũng chọn thẻ đó.
- Đề xuất trùng lặp bị bỏ đi mà không báo gì. Giờ có thông báo "Đã bỏ X/Y đề xuất…".
- Chạy thư viện thị giác (OpenCV/MediaPipe) ngay trong tiến trình backend thỉnh thoảng làm **hỏng cơ sở dữ liệu SQLite**. Đã chuyển sang chạy ở tiến trình con.

**Kiểm thử**
- pytest backend: **166/166 pass**.
- Vitest: **34/34 pass**.
- `npm run build`: pass.
- Smoke test: **61/61 pass**.

**Quyết định quan trọng** (chi tiết trong `DECISIONS.md`)
- Timeline tự vẽ bằng canvas thay cho wavesurfer.js, vì wavesurfer phải giải mã cả file âm thanh trong trình duyệt.
- Khoảnh khắc tạo thủ công mặc định là **đã duyệt**.
- Bộ theo dõi chỉ tự nạp `moments.json` khi nguồn đang ở trạng thái **chờ**. Nếu bạn sửa file sau đó, hãy bấm **Kiểm tra ngay**.
- Xem trước 9:16 tạm thời cắt ở **chính giữa**; Phase 3 sẽ tính khung hình thật.

**Việc bạn nên làm khi thức dậy**
- Chạy thật `/find-moments <id>` trong Claude Code với một video của bạn. Tôi mới chỉ tự viết `moments.json` để thử luồng.
- Nếu muốn dùng nhà cung cấp `anthropic` / `openai`: thêm khoá vào `.env` và thử. Tôi chưa gọi API trả phí nào.

## Phase 3 — Căn khung 9:16 + Trình chỉnh sửa clip (PARTIAL)

**Đã làm được**
- Khi bạn **duyệt** một khoảnh khắc, app tự tạo một clip (EDL), cắt thô từ video gốc (giữ thêm 2 s mỗi đầu) và tự tính khung hình 9:16 trong nền. Việc này chạy ở hàng đợi riêng nên không phải chờ phiên âm.
- Cách tự căn khung theo loại nội dung:
  - Người nói chuyện trước camera → bám khuôn mặt chính.
  - Podcast → bám người đang nói (dựa vào chuyển động miệng), chuyển người thì cắt thẳng.
  - Game có facecam → chia đôi: facecam ở trên, game ở dưới.
  - Quay màn hình → bám con trỏ chuột, phóng to 1.3×. Nếu hiếm khi thấy con trỏ thì hiện cả màn hình trên nền làm mờ.
  - Không thấy ai → tìm người (HOG) → cuối cùng cắt giữa.
  - Video đã dọc → giữ nguyên.
- Chuyển động khung được làm mượt (One-Euro, vùng chết, giới hạn 25 % chiều rộng/giây) và rút gọn còn ít keyframe để dễ sửa tay.
- Trang **Trình chỉnh sửa clip**:
  - Xem trước 9:16 bên trái, video nguồn 16:9 với khung cắt kéo được ở giữa, bảng thuộc tính bên phải, timeline nhiều track ở dưới.
  - Thẻ Khung hình: 4 chế độ, danh sách keyframe sửa số trực tiếp, kiểu chuyển động, Lấp đầy/Làm mờ, chia đôi (kéo, đổi cỡ, vẽ lại 2 vùng; chỉnh tỉ lệ trên/dưới), "Tính lại tự động" (có hỏi xác nhận).
  - Thẻ Âm thanh cơ bản (âm lượng, LUFS mục tiêu, giảm nhạc nền). Các thẻ khác hiện chú thích "có trong bản sau".
  - Phím tắt: S cắt đoạn, Delete xoá đoạn (jump cut), F thêm keyframe, Ctrl+Z/Ctrl+Y, Ctrl+cuộn để phóng timeline.
  - Lớp phủ vùng an toàn cho Shorts/TikTok/Reels. Tự lưu sau 500 ms, hoàn tác 100 bước. "← Khoảnh khắc" quay về đúng vị trí cũ.
- Phần xem trước dùng đúng công thức tính khung như backend (đã kiểm tra bằng bộ số liệu chung cho cả Python và TypeScript). Sửa một keyframe thì xem trước cập nhật sau khoảng 57 ms.

**Vì sao là PARTIAL**
- Điều kiện "clip mẫu bám theo khuôn mặt mượt mà" chưa kiểm tra được trên video thật: file mẫu trong `samples/` là hình test tổng hợp, không có mặt người. Phần bám mặt đã được kiểm tra bằng dữ liệu giả lập trong pytest. Xem `BLOCKED.md`.

**Kiểm thử**
- pytest backend: **200/200 pass**.
- Vitest: **41/41 pass**.
- `npm run build`: pass.
- Smoke test: tất cả pass. Luồng mới: mở clip từ khoảnh khắc → chờ căn khung xong → sửa số keyframe (xem trước đổi ngay, đã lưu) → kéo khung (chuyển sang Thủ công) → S cắt đoạn + Ctrl+Z → vùng an toàn → tiếng Việt không tràn ngang ở cả 6 thẻ → tải lại vẫn còn.

**Quyết định quan trọng** (chi tiết trong `DECISIONS.md`)
- Dùng HOG của OpenCV thay cho YOLO (không phải cài torch và tải model lớn).
- Xem trước bằng canvas thay cho CSS transform, vì canvas hỗ trợ được chia đôi và nền làm mờ. Phase 4 sẽ thay bằng Remotion Player.
- Kéo khung khi đang ở chế độ Tự động sẽ chuyển sang Thủ công. Nếu bạn sửa khung trong lúc máy đang tính lại, bản của bạn được giữ.

**Việc bạn nên làm khi thức dậy**
- Thêm một video có người nói (2–5 phút) vào `samples/`, nhập vào, duyệt một khoảnh khắc rồi mở Trình chỉnh sửa để xem khung có bám mặt mượt không.
