# MACD GC harian — Telegram pribadi Bot 4

Filter ini mencari crossover bullish harian yang baru terbentuk pada snapshot
intraday. Alert merupakan watchlist sementara, bukan order beli atau konfirmasi
penutupan. Crossover yang hilang sebelum penutupan tidak ditarik kembali otomatis.

## Kriteria

Semua syarat harus terpenuhi pada snapshot yang sama.

| Komponen | Syarat |
| --- | --- |
| MACD(12,26,9) | MACD EOD sebelumnya ≤ signal sebelumnya, MACD saat ini > signal saat ini |
| Kenaikan harian | >0% dan <6%, dibanding previous close dari sumber |
| Nilai transaksi | >Rp3 miliar, total transaksi; bukan net foreign buy |
| RSI14 Wilder | ≥40 dan <60, meningkat dari EOD sebelumnya |
| Volume | Volume kumulatif aktual >1,2× EMA20 volume sesi yang telah selesai |
| Harga dan EMA20 | Harga >EMA20 harga termasuk bar sementara; jarak ≤5% |
| Struktur intraday | Harga ≥open; posisi `(harga-low)/(high-low)` ≥65% |
| Batas harga | Mengikuti `MAX_ALERT_PRICE` Bot 4; saat implementasi <Rp500 |
| Riwayat | Minimal 60 sesi selesai; maksimum 120 sesi dipakai |

Formula asli menggunakan RSI<60 dan volume>EMA20. Batas RSI bawah, arah RSI,
rasio volume 1,2×, posisi range, dan jarak EMA adalah pilihan desain awal.
Belum ada bukti backtest bahwa kombinasi ini lebih menguntungkan di BEI.
Volume dibandingkan dengan EMA sesi sebelumnya agar baseline tetap; tidak ada
proyeksi volume harian dari waktu yang telah lewat. Akibatnya sinyal cenderung
muncul setelah volume harian sudah cukup besar.

MACD boleh berada di bawah atau di atas nol; zona ditampilkan sebagai konteks.
EMA disemai dengan SMA periode awal, signal memakai EMA9 MACD, RSI memakai
Wilder14 (RSI flat=50). Nilai dapat sedikit berbeda dengan aplikasi yang memakai
seed atau panjang riwayat berbeda.

## Sumber dan cakupan

Scanner menyaring seluruh baris stock-summary yang sudah diambil dalam siklus
Bot 4, bukan hanya saham Top Mover. Hanya saham dengan arsip memadai yang dapat
dievaluasi. Tidak ada pengunduhan riwayat tambahan ketika arsip kosong.

Arsip dibaca sekali secara bulk melalui `scanner/market_data.py` dan
`MarketArchive.completed_histories`. Data `stock_summary_daily` yang sudah
diarsipkan melengkapi atau menggantikan sesi yang sama dari `stock_history_daily`.
Tanggal ≥hari ini dikeluarkan. Riwayat harus berakhir pada sesi terbaru yang
tersimpan di arsip pasar, tidak lebih tua dari tujuh hari kalender. Close terakhir
harus cocok dengan Previous snapshot; mismatch corporate action/referensi harga,
riwayat tidak cukup, nilai invalid, dan duplikat bertentangan menutup evaluasi.
Tanggal arsip terakhir ditampilkan; arsip terbaru bukan bukti bahwa kalender dan
seluruh sesi per ticker lengkap. Data yang belum tersimpan tidak direkonstruksi.

Tanggal stock-summary harus hari ini WIB. Waktu pemeriksaan adalah waktu scanner,
bukan waktu transaksi terakhir; gateway dapat memberi snapshot cache. Alert
menyatakan sumber cache dan tidak mengklaim tick real-time. Broker, orderbook,
dan aggressive buyer belum dikonfirmasi oleh filter ini.

Filter MACD berdiri sendiri dari seluruh persyaratan Filter 1 Solo. Market cap,
foreign flow, dan skor microstructure tidak menjadi syarat tambahan di sini;
alert tetap harus dinilai sebelum entry.

## Pengiriman dan operasional

- Aktif dengan `MACD_GC_ENABLED=true`, pada workflow scanner yang sudah ada.
- Mengikuti jam pasar regular WIB, termasuk jeda siang dan akhir pekan.
- Target dikunci `private`; tidak ada opsi grup atau fallback ke grup.
- Maksimum satu pengiriman sukses per ticker per tanggal WIB, maksimum tiga
  percobaan kirim per siklus. Kandidat diurutkan berdasarkan rasio volume,
  lalu nilai transaksi, lalu ticker.
- Claim atomik memakai `NeonDailyDelivery`, namespace
  `bot4-macd-gc-private-v1:TICKER`. Kegagalan kirim melepaskan claim agar dapat
  dicoba kembali. Semantik retry/claim terbengkalai tetap mengikuti mekanisme
  yang ada; pengiriman jaringan tidak menjamin exactly-once saat proses terputus.
- Jika tidak ada saham lolos, tidak ada pesan kosong. Log `[MACD-GC-PRIVATE]`
  mencatat prefilter, ketersediaan riwayat, jumlah lolos, terkirim, dan gagal.
- Jika arsip/claim tidak tersedia atau query melewati timeout, filter dilewati
  dengan log peringatan; scanner lain tetap lanjut.

Tidak ada endpoint ZAPI baru, dependensi baru, jadwal baru, atau migrasi skema.
Ada satu pembacaan bulk Neon, perhitungan indikator, dan claim/pencatatan
pengiriman untuk kandidat. Query dibatasi tiga detik; total job tetap empat
menit. Pengiriman dapat menambah sedikit durasi siklus yang sudah ada.

Rollback: set `MACD_GC_ENABLED=false` pada environment workflow (atau revert PR).
Tidak perlu menghapus arsip atau status pengiriman. Pengaktifan ulang pada hari
yang sama tetap menghormati status terkirim sebelumnya.

## Referensi konsep

- [Fidelity: MACD](https://www.fidelity.com/learning-center/trading-investing/technical-analysis/technical-indicator-guide/macd)
  menjelaskan perhitungan 12/26/9, crossover bullish, dan whipsaw pada pasar range.
  Referensi ini mendukung konsep indikator, bukan validasi ambang filter di atas.

## Verifikasi

Tes offline mencakup referensi hitungan indikator, crossover baru, batas filter,
tanggal live/EOD, data tidak valid, harga maksimum, jam pasar, target pribadi,
deduplikasi per ticker/hari, batas per siklus, dan retry. Tes integrasi PostgreSQL
menggunakan database terisolasi di CI untuk membaca gabungan arsip.
Tidak ada pengiriman Telegram produksi atau backtest profitabilitas dalam tes.
