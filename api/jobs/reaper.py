import json
import traceback
from datetime import datetime, timedelta
from typing import List

from parse import parse

from anubis.models import db, Submission, Assignment, Course
from anubis.utils.data import with_context
from anubis.utils.lms.autograde import bulk_autograde
from anubis.utils.lms.submissions import init_submission
from anubis.utils.services.github import fix_github_broken_repos
from anubis.utils.services.logger import logger
from anubis.utils.services.rpc import enqueue_ide_reap_stale, enqueue_autograde_pipeline


def reap_stale_submissions():
    """
    This will set find stale submission and set them to processed. A stale
    submission is one that has not been updated in 15 minutes and is still
    in a processing state.

    Flask app context is required before calling this function.

    :return:
    """

    print("Reaping stale submissions")

    # Find and update stale submissions
    Submission.query.filter(
        Submission.last_updated < datetime.now() - timedelta(minutes=60),
        Submission.processed == False,
        Submission.state != 'regrading',
    ).update({
        'processed': True,
        'state': "Reaped after timeout",
    }, False)

    # Commit any changes
    db.session.commit()


def reap_recent_assignments():
    """
    Calculate stats for recent submissions

    :return:
    """
    from anubis.config import config

    recent_assignments = Assignment.query.filter(
        Assignment.release_date > datetime.now(),
        Assignment.due_date > datetime.now() - config.STATS_REAP_DURATION,
    ).all()

    print(json.dumps({
        'reaping assignments:': [assignment.data for assignment in recent_assignments]
    }, indent=2))

    for assignment in recent_assignments:
        for submission in Submission.query.filter(
                Submission.assignment_id == assignment.id,
                Submission.build == None,
        ).all():
            if submission.build is None:
                init_submission(submission)
                enqueue_autograde_pipeline(submission.id)

    for assignment in recent_assignments:
        bulk_autograde(assignment.id)


def reap_broken_repos():
    """
    For reasons not clear to me yet, the webhooks are sometimes missing
    on the first commit. The result is that repos will be created on
    github without anubis seeing them.

    This function should be the fix for this. It will call out to the
    github api to list all the repos under the organization then try to
    create repos for each listed repo.

    :return:
    """

    # Pull all courses
    courses: List[Course] = Course.query.all()

    # Iterate over all course attempting to fix issues
    # on each github org.
    for course in courses:
        # Get the admin specified github org url
        org_url = (course.github_org_url or '').rstrip('/')

        # Try to parse out the org name from the expected structure
        # of the org url.
        match = parse('https://github.com/{}', org_url)

        # If a match for the org name was not found, then we note in the logs and continue
        if match is None:
            logger.info('Could not find org_name for reaper.reap_broken_repos')
            continue

        # Get the org_name from the matches values
        org_name = match[0]

        # Attempt to fix any broken or lost repos for the course org.
        try:
            fix_github_broken_repos(org_name)
        except Exception as e:
            logger.error('reaper.reap_broken_repos failed', org_name, e)
            logger.error(traceback.format_exc())
            logger.error('continuing')
            continue


@with_context
def reap():
    # Enqueue a job to reap stale ide k8s resources
    enqueue_ide_reap_stale()

    # Reap the stale submissions
    reap_stale_submissions()

    # Reap broken repos
    reap_broken_repos()

    # Reap broken submissions in recent assignments
    reap_recent_assignments()


if __name__ == "__main__":
    print("")
    print("""
             ___
            /   \\\\
       /\\\\ | . . \\\\
     ////\\\\|     ||
   ////   \\\\\\ ___//\\
  ///      \\\\      \\
 ///       |\\\\      |     
//         | \\\\  \\   \\    
/          |  \\\\  \\   \\   
           |   \\\\ /   /   
           |    \\/   /    
           |     \\\\/|     
           |      \\\\|     
           |       \\\\     
           |        |     
           |_________\\  

""")
    reap()
