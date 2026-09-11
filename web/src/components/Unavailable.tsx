/** A feature that cannot be computed says so in one sentence, with the reason the server
 * gave. Used by every analytics card so a data-source failure never looks like a bug. */
export default function Unavailable({ what, reason }: { what: string; reason?: string | null }) {
  return (
    <div className="notice">
      <b>{what} is not available</b>
      {reason ? <>: {reason}</> : <>: the necessary data could not be retrieved.</>}
    </div>
  )
}
