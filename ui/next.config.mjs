const nextConfig = {
  env: { NEXT_PUBLIC_API: process.env.NEXT_PUBLIC_API || "http://127.0.0.1:8000" },
};
export default nextConfig;
