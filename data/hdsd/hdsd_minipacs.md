# HDSD: Hướng dẫn kết nối MiniPacs (CĐHA/PACS)
# Phần mềm quản lý bệnh viện thông minh EHC — E-healthcare Vietnam

---

## Những nội dung cần chuẩn bị

### Phía bệnh viện:
- Chuẩn bị 1 máy trạm có dung lượng lớn (>1TB) dùng để lưu trữ hình ảnh PACS
- Cài đặt các thông số gửi ảnh lên hệ thống PACS và get worklist vào các máy CĐHA
- Thông thường các máy CĐHA kết nối PACS cần cài đặt 2 mục: get worklist từ PACS và đẩy ảnh DICOM lên PACS

### Phía HIS (phần mềm EHC):
- Xin code key MiniPacs từ EHC
- Tạo 2 phòng: Pacs server và Lưu trữ hình ảnh PACS
- Giải nén file Dicom: Trong thư mục bộ cài chính, mở folder "Dicom" (cạnh các folder DLL, FileUpdateData, FileUpdateData_All...), copy tất cả các file bên trong ra ngoài folder bộ cài chính. Folder Dicom chứa các file: EhcView.exe, EhcView.exe.config, MediView.exe, MediView.exe.config, BilinearInterpolation.dll, Sanita.Utility.dll và thư mục con DLL, PacsImage.
- Tại bộ cài webserver: Copy folder DLL và Dicom → Paste vào folder FileUpdateData_All. Folder FileUpdateData_All nằm cùng cấp với Dicom, DLL trong thư mục bộ cài chính.

---

## Kết nối PACS trong phần mềm EHC

### Tạo phòng và cài đặt thông số kết nối

Vào module **PACS Server** (trên thanh menu chính) → Tab **Cấu Hình** → Nhấn **Cấu Hình** bên trái → Cập nhật cấu hình kết nối PACS mới.

Giao diện cấu hình PACS server hiển thị form với các trường:
- **Pacs name**: Nhập tên tự do (ví dụ: pacmini)
- **Pacs AE**: AEtitle của PACS (ví dụ: MediPacs)
- **Pacs port**: Port DICOM (ví dụ: 6666)
- **Worklist port**: Port nhận Worklist (ví dụ: 6868)
- **Thư mục lưu trữ hình ảnh**: Đường dẫn thư mục lưu chỉ định và hình ảnh (ví dụ: D:\EHC\DICOM)
- **URL (Oviyam)**: URL xem ảnh web (ví dụ: http://localhost:6556)
- **Storage folder (Oviyam)**: Thư mục lưu cho Oviyam (ví dụ: D:\EHC\DICOM)
- **Pacs port (Oviyam)**: Port Oviyam (ví dụ: 6677)
- **URL (Dwv)**: URL DWV viewer (ví dụ: http://127.0.0.1:8080)

Nhấn **Lưu** để hoàn thành.

Sau khi cấu hình và khởi động server thành công, màn hình PACS Server tab Cấu Hình sẽ hiển thị:
- Trạng thái: **Đang chạy**
- AE: MediPacs, Host: 192.168.1.15, Port: 6666
- Thư mục: D:\EHC\DICOM
- Số ca chụp: (số hiện tại)
- Giao thức: DICOM, STORE, WORKLIST
- HDD: dung lượng đĩa đang dùng
- Worklist2: Đang chạy, Port WL2: 6868
- Log bên phải hiển thị: MWL Server Started, Đã khởi động server, Start wado server...
- Danh sách máy CĐHA đã khai báo (Tên Máy | AE | Modality)

---

## Cài đặt loại dịch vụ gửi chỉ định sang PACS

Vào module **Danh mục** → **Danh mục loại dịch vụ** → Chọn nhóm **Chẩn đoán hình ảnh** bên trái.

Màn hình hiển thị danh sách loại dịch vụ CĐHA gồm: Siêu âm tim (SA_TIM), X-Quang Số Hóa (XQSH), X-quang (X_QUANG), Siêu âm (SIEU_AM), Nội soi (NOI_SOI), Điện tim (DIEN_TIM), Điện não (DIEN_NAO), CT Scan (CT_SCAN), MRI (Cộng hưởng từ), Loãng xương (LOANG_XUONG), Thăm dò chức năng (TDCN), Thủ thuật CĐHA (TTCDHA), Xa hình (XA_HINH), Can thiệp mạch (CAN_THIEP_MACH), Dịch vụ khác (CDHA_KHAC). Cột "Cho Phép Gửi Sang PACS" hiển thị trạng thái cấu hình.

Chọn loại dịch vụ cần cấu hình (ví dụ: X-quang, mã X_QUANG, ID 401) → Form cập nhật hiện ra với:
- ID: 401, Nhóm dịch vụ: Chẩn đoán hình ảnh
- Mã loại dịch vụ: X_QUANG, Tên loại dịch vụ: X-quang
- Thứ tự trong nhóm: 30
- Modality máy CĐHA: (để trống hoặc điền modality tương ứng)
- Checkbox **"Gửi chỉ định sang PACS"**: Tích chọn để gửi bản tin ORDER và DELETE sang PACS
- Checkbox **"Gửi kết quả sang PACS"**: Tích chọn để gửi bản tin REPORT sang PACS

Nhấn **Lưu** để hoàn thành.

---

## Mở kết nối và thông kết nối

Sau khi đã cài đặt xong thông số và phía PACS đã sẵn sàng nhận kết nối:
1. Khởi động lại phần mềm EHC
2. Vào module PACS Server → Nhấn **Bật Server**
3. Nếu kết nối thành công: Log bên phải hiển thị "MWL Server Started", "Đã khởi động server", "Start wado server..." — trạng thái chuyển sang "Đang chạy"
4. Nếu bản tin gửi đi bị lỗi (chờ ACK lâu, timeout): Check lại log, kiểm tra lại AEtitle, port DICOM, worklist port và cấu hình phía máy CĐHA

---

## Khai báo máy CĐHA

Trong module PACS Server → Tab Cấu Hình → Phần **Danh Sách Máy CĐHA** → Nhấn link **Cấu Hình**.

Màn hình Danh Sách Máy CĐHA mở ra, các bước:
1. Nhấn nút **+ Thêm** trên toolbar
2. Form "EHC - Cập Nhật Máy CĐHA" hiện ra với các trường:
   - **Tên máy CĐHA**: Ghi tên máy để dễ phân biệt (ghi tự do, ví dụ: XQ, CT, MRI)
   - **AETitle**: Ghi AEtitle của máy CĐHA (nếu đẩy ảnh trực tiếp từ HIS thì AEtitle là MediBox)
   - **Modality**: Chọn modality phù hợp
3. Nhấn **Lưu** để hoàn thành

Các giá trị Modality:
- **DX**: Máy X-Quang
- **US**: Máy siêu âm
- **MRI**: Máy MRI (Cộng hưởng từ)
- **CT**: Máy CT Scan
- **ECG**: Máy điện tim

Ví dụ máy đã khai báo: XQ (AE: MediBox, Modality: DX), XQ (AE: MEDIBOX, Modality: DX), CT (AE: MediBox, Modality: CT), CT (AE: MEDIBOX, Modality: CT), DX (AE: MiniPacs, Modality: DX).

---

## Xử lý lỗi thường gặp

- **Bản tin gửi đi bị lỗi hoặc timeout**: Kiểm tra AEtitle, Pacs port, Worklist port, kết nối mạng
- **Không get được worklist**: Kiểm tra worklist port và cấu hình phía máy CĐHA
- **Hình ảnh không hiển thị trên PACS**: Kiểm tra thư mục lưu trữ (Thư mục lưu trữ hình ảnh) và kết nối mạng giữa máy trạm PACS và server HIS
- **Kết nối đóng tự động**: Khởi động lại phần mềm và bật lại server PACS
