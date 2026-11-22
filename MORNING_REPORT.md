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
