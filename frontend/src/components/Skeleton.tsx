type Props = {
  width?: number | string;
  height?: number | string;
  style?: React.CSSProperties;
};

export function Skeleton({ width = "100%", height = 16, style }: Props) {
  return <div className="skeleton" style={{ width, height, ...style }} />;
}
