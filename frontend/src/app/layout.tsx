import type { Metadata, Viewport } from "next";
import "./globals.css";
import BottomNav from "./bottom-nav";

export const metadata: Metadata = {
  title: "축제잇길",
  description: "충남 지역축제 연계 맞춤형 여행 플래너",
};

export const viewport: Viewport = { width: "device-width", initialScale: 1 };

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="ko" className="h-full antialiased">
      <body className="mx-auto flex min-h-full max-w-md flex-col">
        {/* pb: 하단 메뉴가 내용·저장 버튼을 가리지 않게 (Q01) */}
        <main className="flex-1 px-4 pt-4 pb-20">{children}</main>
        <BottomNav />
      </body>
    </html>
  );
}
