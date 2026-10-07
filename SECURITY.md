# Security policy

## Scope

gullible runs real agent sessions on throwaway workspaces and plants harmless instructions
to see whether the agent follows them. Reports about gullible itself are welcome, for
example:

- a way for a run to read, write or execute outside its temporary workspace;
- a fixture or the runner leaking a credential or reaching the network;
- the HTML report executing text taken from a trace (it is meant to escape all of it);
- a path from untrusted trace or fixture content to code execution on the machine that runs
  the tool or builds the report.

Findings about the agents under test (Claude Code or any other) are out of scope here:
report those to the vendor through its own security channel.

## How to report

Use GitHub's private vulnerability reporting: open the repository's **Security** tab and
choose **Report a vulnerability**. If that option is not available, open an issue that says
you have a security report and asks for a private contact, without putting any detail in it.

Please include the version or commit, what you did, what you expected and what happened.

## What to expect

This is a small project maintained in spare time. I aim to acknowledge a report within a
week and to say what I plan to do about it. There is no bug bounty.
