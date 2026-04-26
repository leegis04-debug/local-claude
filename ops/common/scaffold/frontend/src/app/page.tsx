export default function Home() {
  const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8080';
  return (
    <main style={{ padding: '2rem' }}>
      <h1>__PROJECT__</h1>
      <p>Spring Backend: <code>{apiUrl}</code></p>
      <p>이 화면을 채워넣으세요. <code>src/app/page.tsx</code></p>
    </main>
  );
}
