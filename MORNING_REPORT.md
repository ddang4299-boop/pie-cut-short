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
