Feature: Review queue specs

    The criteria builder is the only way to write a spec without knowing
    the DSL, and it is entirely client-side: the page ships an empty
    form, and lute-review-criteria.js fills it in from JSON tags the
    form partial renders later in the document.  Unit tests cover the
    serialization, but they stub the DOM, so they cannot see load order.
    These scenarios drive the real page.


    Background:
        Given a running site
        And demo languages


    Scenario: A new spec opens with the builder already filled in
        Given I visit "/review/spec/new"
        Then the review builder shows 2 conditions
        And the review builder shows the preset "Learning levels 1-5"
        And the review criteria are "status >= 1 and status <= 5"


    Scenario: A preset fills the conditions, and editing one drops the preset
        Given I visit "/review/spec/new"
        When I choose the review preset "Level 2 and above"
        Then the review builder shows 1 condition
        And the review builder shows the preset "Level 2 and above"
        And the review criteria are "status >= 2"
        When I set review condition 1 to "4"
        Then the review criteria are "status >= 4"
        And the review builder shows the preset "(choose a starting point)"


    Scenario: A spec built from the dropdowns saves and appears on the dashboard
        Given I visit "/review/spec/new"
        When I choose the review preset "Well-known only"
        And I save the review spec named "Mastered"
        Then the review spec list shows "Mastered" with criteria "status == 99"
