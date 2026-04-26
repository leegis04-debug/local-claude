export const metadata = {
  title: '__PROJECT__',
  description: 'Spring AI + Python AI Worker',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ko">
      <body>{children}</body>
    </html>
  );
}
