Feature: Review session

    The session runner is client-side: the page ships an empty shell and
    lute-review.js fills it from POST /review/start, then posts each
    grade.  A card that never renders looks exactly like a session with
    nothing due, so these scenarios read the live DOM rather than the
    markup.

    Keywords are explicit here because pytest-bdd matches a step only
    under the keyword it was registered with, and `I visit` is a Given
    throughout this suite.


    Background:
        Given a running site
        And demo languages


    Scenario: Sync a spec into the queue, grade a card, and undo the grade
        Given a new Spanish term:
            text: gato
            translation: cat
        Given I visit "/review/spec/new"
        When I choose the review preset "All learning terms"
        And I save the review spec named "Everything"
        And I sync the review queue
        Then the review dashboard shows 1 new card waiting
        Given I visit "/review/session"
        Then the review card shows the term "gato"
        When I reveal the review answer
        And I grade the card "Good"
        Then the review session is done
        And the undo button is available
        When I undo the last grade
        Then the review card shows the term "gato"
