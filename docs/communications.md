# Communications from Nebius

## Final stretch — here's your submission checklist ⏰ - Nebius x NVIDIA Global AI Hackathon
##### Submissions are due Friday, October 30 at 10:00 AM PT.

 ![📋](https://fonts.gstatic.com/s/e/notoemoji/17.0/1f4cb/32.png)**Submission checklist:**

-   Does your project run on Nebius Token Factory or Nebius AI Cloud, and use at least one NVIDIA open source model?
-   Does it fit clearly into one of the four tracks (Coding and Agentic Engineering, Best Apps and Agents, Personal AI, Physical AI)?
-   Have you created a demo video under 3 minutes, public on YouTube, that shows your app actually working and explains your Nebius + NVIDIA usage?
-   Is your code repository public with a visible open-source license, plus a README with setup and execution instructions?
-   Do you have a working demo URL? (Not required for Physical AI — but Physical AI needs at least 1 minute of footage of your hardware actually operating.)
-   Have you filled out your feedback on the Nebius tools and NVIDIA tech you used? This goes straight to the Nebius and NVIDIA teams, so make it specific.
-   Are all your teammates added to your submission form? Remind them to accept the invitation!

 ![🚨](https://fonts.gstatic.com/s/e/notoemoji/17.0/1f6a8/32.png)**Don't leave your video upload to the last minute.** Video files are large, and uploads can take much longer than expected. Get it uploaded today, even if you're still polishing the rest of your project.

 ![⚠️](https://fonts.gstatic.com/s/e/notoemoji/17.0/26a0_fe0f/32.png)**Once the deadline passes, submissions are locked.** Please do not edit your code repo, video, or any project materials after the deadline — even minor changes could affect your eligibility.

Judging runs December 1–15, and winners will be announced **Monday, January 11, 2027**. Good luck — we can't wait to see what you've built!

## Important advisory on recent industry security disclosures
from render.com

Hello,

This week, major security issues were disclosed in software and infrastructure widely used across the internet. These issues may require you to update your applications. Review the guidance below and update any affected applications as soon as possible.

**Render's platform remains secure, and we are not aware of any breaches or compromises. If you have already updated your applications and dependencies, you do not need to take additional action.**

## What's potentially affected

-   Any application that sends HTTPS requests to destinations over the internet
-   Any application running Node.js, regardless of framework
-   Any application running PHP, regardless of framework

## What happened

**eMudhra certificate compromise:** On October 19, a public disclosure confirmed the compromise of private keys for eight intermediate certificates issued by eMudhra, a widely trusted certificate authority. Attackers can use these keys to impersonate websites and services to applications that still trust the affected certificates, circumventing the protection normally provided by HTTPS.

**Node.js security release:** On October 21, Node.js released patches for multiple vulnerabilities, including flaws that can bypass TLS certificate hostname checks and a flaw in Brotli decompression that can allow remote code execution. These releases also address the eMudhra certificate compromise.

**PHP2Shell:** On October 21, PHP released patches for two critical vulnerabilities discovered in the PHP runtime. These vulnerabilities can allow unauthenticated attackers to execute code on affected services or read sensitive information from memory, including passwords, tokens, and API keys. PHP applications can be affected regardless of which framework they use.

## What you need to do

1.  **Update affected dependencies and rebuild your services.**

    Follow our [eMudhra advisory](https://track.pstmrk.it/3s/render.com%2Fdocs%2Fadvisory-emudhra/gtlU/RP7IAQ/AQ/71649184-73c5-4b7d-8399-12e43efb0957/2/NOktq38sHJ) for instructions specific to your runtime. For all native runtimes, rebuilding your service updates its system certificate store. However, some languages and libraries bundle their own certificates and require separate updates. For Docker-based services, update the certificate store and affected dependencies in your image before redeploying.

2.  **If you run Node.js, update its version and redeploy every affected service.**

    Update to `22.23.4`, `24.22.1`, or `26.12.1`, or a later release in the same major version. These are the same versions recommended in our eMudhra advisory. For native runtimes, set your service's [Node.js version](https://track.pstmrk.it/3s/render.com%2Fdocs%2Fnode-version/gtlU/RP7IAQ/AQ/71649184-73c5-4b7d-8399-12e43efb0957/3/e6SovAdMI3). For Docker services, update Node.js in your image.

3.  **If you run PHP, update its version and redeploy every affected service.**

    All PHP apps on Render use Docker. Follow our [PHP2Shell advisory](https://track.pstmrk.it/3s/render.com%2Fdocs%2Fadvisory-php2shell/gtlU/RP7IAQ/AQ/71649184-73c5-4b7d-8399-12e43efb0957/4/2kAdp3DMoX) to update the PHP installation in your Docker image and confirm that your deployed services use a patched version.


We will update these advisories as additional fixes and guidance become available. Please refer to them for the latest instructions.

Under Render's [Shared Responsibility Model](https://track.pstmrk.it/3s/render.com%2Fdocs%2Fshared-responsibility-model/gtlU/RP7IAQ/AQ/71649184-73c5-4b7d-8399-12e43efb0957/5/fidwkrGDxO), customers are responsible for the security of their application-level configurations, dependencies, and secrets. This includes ensuring that the packages and libraries your applications depend on are secure and up to date.

If you have questions or need assistance, contact our support team in the [Render Dashboard](https://track.pstmrk.it/3s/dashboard.render.com%3Fcontact-support/gtlU/RP7IAQ/AQ/71649184-73c5-4b7d-8399-12e43efb0957/6/bd-_irX3Gp).

Thank you,
The Render Team