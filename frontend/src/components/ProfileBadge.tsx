export default function ProfileBadge({
  profile,
  enabled,
  total,
}: {
  profile: string;
  enabled: number;
  total: number;
}) {
  return (
    <div className="flex items-center gap-2">
      <span className="rounded-full bg-slate-800 px-2.5 py-0.5 text-xs font-medium uppercase tracking-wide text-white">
        {profile} profile
      </span>
      <span className="text-xs text-slate-500">
        {enabled}/{total} capabilities
      </span>
    </div>
  );
}
