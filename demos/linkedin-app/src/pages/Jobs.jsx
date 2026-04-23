import { useLinkedIn } from '../context/LinkedInContext'
import JobCard from '../components/JobCard'

function Jobs() {
  const { jobs } = useLinkedIn()

  return (
    <div className="jobs-page">
      <div className="jobs-header">
        <h2>Jobs picked for you</h2>
        <p>Based on your profile, preferences, and activity</p>
      </div>
      <div className="jobs-list">
        {jobs.map(job => (
          <JobCard key={job.id} job={job} />
        ))}
      </div>
    </div>
  )
}

export default Jobs
