import { redirect } from "next/navigation";

export default async function LegacyTranslate({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  redirect(`/papers/${id}?mode=bilingual`);
}
