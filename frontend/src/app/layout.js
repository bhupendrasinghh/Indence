import "./globals.css";

export const metadata = {
  title: "Indence | Grounded Oncology Evidence Engine",
  description: "Clinical evidence synthesis with math-grounded NLI verification and post-hoc coordinate highlighting.",
};

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body>
        {children}
      </body>
    </html>
  );
}
