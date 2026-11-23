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

## Phase 4 — Phụ đề + Remotion ✅

**Đã làm được**
- Dự án **Remotion** trong `remotion/`: một composition 1080×1920 nhận EDL (kiểm tra bằng Zod) và vẽ video nguồn đã cắt khung theo keyframe, phụ đề, câu mở đầu (mặc định 0–3,5 s) và thanh tiến trình ở dưới. Đã render thử 1 khung hình bằng Remotion CLI: chữ tiếng Việt in hoa vẫn đúng dấu (ĐẶC BIỆT, Đừng bỏ lỡ điều này).
- 3 kiểu phụ đề, đều tô sáng từng từ theo thời gian nói (karaoke), font **Be Vietnam Pro**:
  - **Bold**: IN HOA (in hoa theo tiếng Việt), viền đen dày, từ đang nói màu vàng, cả khối bật lên bằng spring.
  - **Bounce**: từng từ nảy lên khi được nói, màu sắc xoay vòng.
  - **Clean**: chữ thường tối giản, hiện dần.
- `captions.py`: chia 1–4 từ mỗi khối (ngắt ở dấu câu, khoảng lặng > 0,6 s và chỗ cắt jump cut), xuất **SRT** (cho CapCut) và **ASS** (cho đường render dự phòng FFmpeg).
- Trình chỉnh sửa clip:
  - Xem trước 9:16 giờ dùng **@remotion/player** (chung composition với bản render), cập nhật ngay khi sửa.
  - Thẻ **Phụ đề**: 3 thẻ kiểu có xem trước động, cỡ chữ, vị trí dọc (kéo trực tiếp đường nét đứt trên khung xem trước), số từ mỗi khối, danh sách khối phụ đề (sửa chữ, sửa thời gian, bấm để nhảy tới), bấm vào từ để nhấn mạnh, tìm và thay thế, tải file SRT.
  - Thẻ **Câu mở đầu**: bật/tắt, nội dung, thời gian hiện/ẩn, vị trí.
  - Thẻ **Xuất**: nền tảng, thanh tiến trình bật/tắt và màu. (Nút Render có ở Phase 6.)
  - Track Phụ đề trên timeline hiện từng khối.
- File `captions.srt` cạnh clip tự cập nhật mỗi lần lưu.

**Kiểm thử**
- pytest backend: **207/207 pass** (thêm 7 bài cho phụ đề: chia khối, jump cut, SRT, ASS, gộp từ, API).
- Vitest: **51/51 pass** (bộ số liệu chung Python ↔ TypeScript cho cách chia khối).
- `npm run build`: pass. Kiểm tra khoá en/vi: pass.
- Smoke test: **80 kiểm tra pass**. Luồng mới: sửa chữ một khối thành "Đặc biệt quá" và một khối tiếng Anh → xem trước hiện đúng ở cả 3 kiểu (Bold in hoa đúng dấu) → nhấn mạnh từ → tìm/thay thế → kéo vị trí phụ đề → file SRT có nội dung mới → câu mở đầu hiện ở giây 0 → tiếng Việt không tràn ngang ở mọi thẻ.

**Quyết định quan trọng** (chi tiết trong `DECISIONS.md`)
- Xem trước kiểu "lai": video vẫn vẽ bằng canvas từ một thẻ video duy nhất, còn phụ đề / câu mở đầu / thanh tiến trình là Remotion Player trong suốt đặt lên trên. Như vậy không có hai bộ giải mã video bị lệch nhau, và lớp chữ chính là component dùng khi render.
- Từ phụ đề lưu trong EDL theo thời gian của video gốc, nên cắt/ghép đoạn không làm mất chỗ bạn đã sửa.
- Từ được nhấn mạnh thì to hơn thật (không dùng phóng bằng transform), để các từ không đè lên nhau.

**Lưu ý**
- Bản render thử dùng trình duyệt Edge có sẵn trên máy; Phase 6 sẽ chốt cách render đầy đủ.

## Phase 5 — ElevenLabs (lồng tiếng) ⚠️ PARTIAL

**Đã làm được**
- `voice.py` + `voice_service.py`: tạo lồng tiếng (TTS) bằng SDK chính thức `elevenlabs`, danh sách giọng (có nghe thử), Voice Isolator (tách giọng nói), xem mức credit đã dùng.
  - Mô hình mặc định **Flash v2.5** (có tiếng Việt, ½ credit/ký tự). Multilingual v2 **không có tiếng Việt** nên được ghi rõ trong danh sách.
  - **Bộ nhớ đệm theo hash** (kịch bản + giọng + mô hình + thiết lập): tạo lại cùng nội dung **không tốn credit**, không gọi API.
  - **Luôn hỏi trước khi tốn credit**: hộp xác nhận hiện số ký tự, số credit ước tính và số credit còn lại. Nếu đã có trong bộ nhớ đệm thì dùng luôn, không hỏi.
  - Lỗi được dịch EN/VI: chưa có khoá, khoá sai, hết credit, thiếu quyền, quá nhiều yêu cầu, mất mạng, giọng không tồn tại… Không có khoá thì app vẫn chạy bình thường, chỉ tắt tính năng giọng.
- Thẻ **Giọng nói** trong trình chỉnh sửa: kịch bản (đếm ký tự + credit), nút **Gợi ý kịch bản** (từ transcript + hook, theo ngôn ngữ video), chọn giọng + nghe thử, chọn mô hình, thanh trượt độ ổn định / độ giống / phong cách / tốc độ, nút Tạo, vị trí bắt đầu (gõ, đặt tại đầu phát, hoặc **kéo khối xanh trên track Lồng tiếng**), âm lượng, ducking (mặc định −12 dB), báo khi kịch bản đã đổi so với bản đã tạo.
- **Xem trước có tiếng**: lồng tiếng phát cùng video, âm thanh gốc tự giảm (ducking) khi lồng tiếng phát; bật tách giọng thì nghe giọng đã tách thay cho âm gốc.
- Thẻ **Âm thanh**: công tắc Tách giọng nói (ước tính credit, hỏi trước).
- **Cài đặt**: mục Lồng tiếng ElevenLabs (trạng thái khoá, mức dùng, giọng mặc định, mô hình mặc định).
- Chế độ **giả lập** `ELEVENLABS_MOCK=1` (âm thử, không gọi mạng, không tốn credit) để kiểm thử.

**Kiểm thử**
- pytest backend: **221/221 pass** (thêm 14 bài: ước tính, phân loại lỗi, bộ nhớ đệm, xác nhận trước khi tốn, chặn đường dẫn lạ, hết credit, tách giọng).
- Vitest 51/51, `npm run build` pass, kiểm tra khoá en/vi pass.
- Smoke test (backend chạy giả lập): **97 kiểm tra pass**. Luồng mới: viết kịch bản tiếng Việt → thấy ước tính credit → bấm Tạo → hộp xác nhận hiện đúng số ký tự/credit, chưa tốn gì → xác nhận → khối lồng tiếng hiện trên timeline → bấm phát: nghe lồng tiếng, âm gốc giảm đúng −12 dB → tạo lại cùng kịch bản: 0 credit, không hỏi, không gọi API → kéo khối + Ctrl+Z → bật/tắt tách giọng → trang Cài đặt hiện trạng thái khoá.

**Cần bạn làm** (ghi trong `BLOCKED.md`)
- Thử với **khoá ElevenLabs thật**: theo quy tắc tôi không được gọi API thật (tốn credit) và `.env` chưa có khoá. Thêm `ELEVENLABS_API_KEY` vào `.env`, khởi động lại backend (không bật `ELEVENLABS_MOCK`), tạo thử một câu ngắn.

**Lưu ý**
- Chưa làm ElevenLabs Scribe (chuyển giọng nói thành chữ) vì không bắt buộc; faster-whisper đã chạy tốt và miễn phí.

## Phase 6 — Render + Export + CapCut ⚠️ PARTIAL

**Đã làm được**
- **Kết xuất (render)** 1080×1920 theo EDL, hai cách:
  - **Remotion** (mặc định): phụ đề karaoke, hiệu ứng lò xo, hook, thanh tiến trình, giống hệt bản xem trước.
  - **FFmpeg + ASS** (nhanh hơn, dự phòng): nếu Remotion lỗi thì tự chuyển sang FFmpeg và ghi cảnh báo.
- **Bộ mã hoá**: NVENC / VideoToolbox nếu có, không thì libx264. GPU lỗi thì tự chuyển sang libx264 kèm cảnh báo.
- **Âm thanh**: trộn âm gốc + lồng tiếng + ducking giống bản xem trước, chuẩn hoá về **−14 LUFS** (2 lượt loudnorm + một lượt chỉnh bù nếu lệch > 0,4 LU), true peak ≤ −1,5 dBTP.
- **Tự kiểm tra sau mỗi lần render**: so 3 khung hình với video gốc (SSIM ≥ 0,95) và đo lại độ to. Nếu lệch thì hiện cảnh báo, video vẫn được giữ.
- **Preset nền tảng** (YouTube Shorts / TikTok / Reels): bitrate, độ dài tối đa. Clip quá dài thì cảnh báo ngay trong trình chỉnh sửa và sau khi render.
- Trình chỉnh sửa → thẻ **Xuất**: chọn bộ render, nút **Render**, tiến độ trực tiếp. Bấm Render nghĩa là duyệt bản chỉnh sửa.
- Trang **Xuất**:
  - Hàng đợi có %, thời gian còn lại, bộ render, bộ mã hoá, nút Huỷ.
  - Danh sách clip: ảnh thu nhỏ, Phát, Tải MP4, Tải SRT, Mở thư mục, chọn nhiều → **Render các clip đã chọn**.
- **Gói CapCut**: `clip_9x16_clean.mp4` (không phụ đề), `clip_final.mp4`, `captions.srt`, `original_audio.wav`, `voiceover.mp3`, `README.txt` hướng dẫn **tiếng Anh + tiếng Việt**, kèm file zip.
- **Nhập lại bản đã sửa trong CapCut**: tải file lên, kiểm tra đúng là video rồi mới lưu thành `clip_capcut.mp4`; bản render gốc vẫn giữ.
- **Dọn dẹp**: thư mục tạm bị xoá sau mỗi lần render (kể cả khi huỷ hoặc lỗi). Mở lại app sau khi tắt giữa chừng thì tác vụ dở được đánh dấu "đã huỷ" và dọn file tạm.
- Đã sửa lỗi lệch 1 khung hình giữa FFmpeg và Remotion (giờ lệch 0), và lỗi file thiếu nhãn màu BT.709.

**Kiểm thử**
- pytest backend: **240/240 pass** (thêm 19 bài: preset, trộn âm, loudnorm, bố cục khung hình, bộ mã hoá dự phòng, README CapCut, và 2 bài chạy thật: render → gói CapCut → tải file → nhập bản sửa → render hàng loạt → huỷ → dọn dẹp khi khởi động).
- Vitest 51/51, `npm run build` pass, kiểm tra khoá en/vi pass.
- Render thật qua API: một clip 14 s bằng FFmpeg mất khoảng 43 s, đo được **−14,5 LUFS**, true peak −1,6 dBTP, SSIM 0,997–0,998, nhãn màu bt709 đầy đủ.

**Cần bạn làm** (ghi trong `BLOCKED.md`)
- Phát thử MP4 **trên điện thoại** (màu, âm lượng, vùng an toàn của phụ đề).
- Mở **gói CapCut** trong CapCut bản máy tính (máy này chưa cài CapCut).
- **NVENC**: driver NVIDIA hiện là 560.94, cần bản ≥ 610 thì FFmpeg mới dùng được GPU để mã hoá. Hiện app tự dùng CPU (libx264).

**Lưu ý**
- Remotion chậm hơn mục tiêu trên máy này (clip 18 s mất 74 s, nên clip 45 s mất khoảng 3 phút). Nếu cần nhanh, chọn **FFmpeg (nhanh hơn)** trong Cài đặt hoặc ngay trong thẻ Xuất.
- **CapCut mức 2** (tạo sẵn project nháp) đã bỏ qua: CapCut chưa cài, và các bản CapCut mới mã hoá file nháp nên thư viện tạo nháp không còn dùng được.
