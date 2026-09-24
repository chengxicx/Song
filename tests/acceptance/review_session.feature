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


    Scenario: A learning term is auto-admitted, graded, and ungraded
        Given a new Spanish term:
            text: gato
            translation: cat
        And I visit "/review/index"
        Then the review dashboard shows 0 due cards
        Given I visit "/review/session"
        Then the review card shows the term "gato"
        # Nothing has been graded yet, so Undo must not be on screen at
        # all -- not merely flagged hidden in the markup.
        And the undo button is hidden
        When I reveal the review answer
        And I grade the card "Good"
        Then the review session is done
        And the undo button is available
        When I undo the last grade
        Then the review card shows the term "gato"
