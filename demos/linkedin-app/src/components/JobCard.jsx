import { useLinkedIn } from '../context/LinkedInContext'

function JobCard({ job }) {
  const { savedJobIds, toggleSaveJob } = useLinkedIn()
  const isSaved = savedJobIds.has(job.id)

  return (
    <div className="job-card">
      <img src={job.logo} alt={job.company} className="job-logo" />
      <div className="job-info">
        <h4 className="job-title">{job.title}</h4>
        <p className="job-company">{job.company}</p>
        <p className="job-location">{job.location}</p>
        <p className="job-salary">{job.salary}</p>
        <div className="job-meta">
          <span className="job-posted">{job.posted}</span>
          <span className="job-applicants">{job.applicants} applicants</span>
        </div>
      </div>
      <button
        className={`save-job-btn ${isSaved ? 'saved' : ''}`}
        onClick={() => toggleSaveJob(job.id)}
        title={isSaved ? 'Unsave job' : 'Save job'}
      >
        <svg viewBox="0 0 24 24" width="20" height="20" fill={isSaved ? 'currentColor' : 'none'} stroke="currentColor" strokeWidth="2">
          <path d="M19 21l-7-5-7 5V5a2 2 0 012-2h10a2 2 0 012 2z"/>
        </svg>
      </button>
    </div>
  )
}

export default JobCard
